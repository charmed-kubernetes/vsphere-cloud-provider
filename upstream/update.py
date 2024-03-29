#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Update to a new upstream release."""
import argparse
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from itertools import accumulate
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Generator, List, Optional, Set, Tuple, TypedDict

import yaml
from semver import VersionInfo

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
GH_REPO = "https://api.github.com/repos/{repo}"
GH_TAGS = "https://api.github.com/repos/{repo}/tags"
GH_BRANCH = "https://api.github.com/repos/{repo}/branches/{branch}"
GH_COMMIT = "https://api.github.com/repos/{repo}/commits/{sha}"
GH_RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{path}/{rel}/{manifest}"


def _ver_maker(v: str) -> Tuple[int, ...]:
    return tuple(map(int, v.split(".")))


SOURCES = dict(
    cloud_provider=dict(
        repo="kubernetes/cloud-provider-vsphere",
        manifest="vsphere-cloud-controller-manager.yaml",
        default_branch=True,
        path="releases",
        version_parser=lambda v: _ver_maker(v[1:]),
        minimum="v1.2",
    ),
    cloud_storage=dict(
        repo="kubernetes-sigs/vsphere-csi-driver",
        manifest="vsphere-csi-driver.yaml",
        release_tags=True,
        path="manifests/vanilla",
        version_parser=VersionInfo.parse,
        minimum="v2.5.1",
    ),
)
FILEDIR = Path(__file__).parent
VERSION_RE = re.compile(r"^v\d+\.\d+")
IMG_RE = re.compile(r"^\s+image:\s+(\S+)")


@dataclass(frozen=True)
class Registry:
    """Object to define how to contact a Registry."""

    name: str
    path: str
    user: str
    pass_file: str

    @property
    def creds(self) -> "SyncCreds":
        """Get credentials as a SyncCreds Dict."""
        return {
            "registry": self.name,
            "user": self.user,
            "pass": Path(self.pass_file).read_text().strip(),
        }


@dataclass(frozen=True)
class Release:
    """Defines a release type."""

    name: str
    path: Path = Path()
    size: int = 0
    upstream: str = ""

    def __hash__(self) -> int:
        """Unique based on its name."""
        return hash(self.name)

    def __eq__(self, other) -> bool:
        """Comparable based on its name."""
        return isinstance(other, Release) and self.name == other.name

    def __lt__(self, other) -> bool:
        """Compare version numbers."""
        a, b = self.name[1:], other.name[1:]
        try:
            return VersionInfo.parse(a) < VersionInfo.parse(b)
        except ValueError:
            return _ver_maker(a) < _ver_maker(b)


SyncAsset = TypedDict("SyncAsset", {"source": str, "target": str, "type": str})
SyncCreds = TypedDict("SyncCreds", {"registry": str, "user": str, "pass": str})


class SyncConfig(TypedDict):
    """Type definition for building sync config."""

    version: int
    creds: List[SyncCreds]
    sync: List[SyncAsset]


def sync_asset(image: str, registry: Registry):
    """Factory for generating SyncAssets."""
    _, tag = image.split("/", 1)
    dest = f"{registry.name}/{registry.path.strip('/')}/{tag}"
    return SyncAsset(source=image, target=dest, type="image")


def main(source: str, registry: Optional[Registry]):
    """Main update logic."""
    local_releases = gather_current(source)
    latest, gh_releases = gather_releases(source)
    new_releases = gh_releases - local_releases
    for release in new_releases:
        local_releases.add(download(source, release))
    unique_releases = list(dict.fromkeys(accumulate((sorted(local_releases)), dedupe)))
    all_images = set(image for release in unique_releases for image in images(release))
    if registry:
        mirror_image(all_images, registry)
    return latest, all_images


def gather_releases(source: str) -> Tuple[str, Set[Release]]:
    """Fetch from github the release manifests by version."""
    context = dict(**SOURCES[source])
    version_parser = context["version_parser"]
    if context.get("default_branch"):
        with urllib.request.urlopen(GH_REPO.format(**context)) as resp:
            context["branch"] = json.load(resp)["default_branch"]
        with urllib.request.urlopen(GH_BRANCH.format(**context)) as resp:
            branch = json.load(resp)
            context["sha"] = branch["commit"]["sha"]
        with urllib.request.urlopen(GH_COMMIT.format(**context)) as resp:
            commit = json.load(resp)
            tree_url = commit["commit"]["tree"]["url"]
        for part in Path(context["path"]).parts:
            with urllib.request.urlopen(tree_url) as resp:
                tree = json.load(resp)
                tree_url = next(item["url"] for item in tree["tree"] if item["path"] == part)
        with urllib.request.urlopen(tree_url) as resp:
            releases = sorted(
                [
                    Release(item["path"], upstream=GH_RAW.format(rel=item["path"], **context))
                    for item in json.load(resp)["tree"]
                    if VERSION_RE.match(item["path"])
                    and version_parser(context["minimum"]) <= version_parser(item["path"])
                ],
                key=lambda r: version_parser(r.name),
                reverse=True,
            )
    elif context.get("release_tags"):
        with urllib.request.urlopen(GH_TAGS.format(**context)) as resp:
            releases = sorted(
                [
                    Release(
                        item["name"],
                        upstream=GH_RAW.format(branch=item["name"], rel="", **context),
                    )
                    for item in json.load(resp)
                    if (
                        VERSION_RE.match(item["name"])
                        and not version_parser(item["name"][1:]).prerelease
                        and version_parser(context["minimum"][1:])
                        <= version_parser(item["name"][1:])
                    )
                ],
                key=lambda r: version_parser(r.name[1:]),
                reverse=True,
            )

    return releases[0].name, set(releases)


def gather_current(source: str) -> Set[Release]:
    """Gather currently supported manifests by the charm."""
    manifest = SOURCES[source]["manifest"]
    return set(
        Release(release_path.parent.name, release_path, release_path.stat().st_size)
        for release_path in (FILEDIR / source / "manifests").glob(f"*/{manifest}")
    )


def download(source: str, release: Release) -> Release:
    """Download the manifest files for a specific release."""
    log.info(f"Getting Release {source}: {release.name}")
    manifest = SOURCES[source]["manifest"]
    dest = FILEDIR / source / "manifests" / release.name / manifest
    dest.parent.mkdir(exist_ok=True)
    urllib.request.urlretrieve(release.upstream, dest)
    return Release(release.name, dest, release.size)


def dedupe(this: Release, next: Release) -> Release:
    """Remove duplicate releases.

    returns this release if this==next by content
    returns next release if this!=next by content
    """
    if this.path.read_text() != next.path.read_text():
        # Found different in at least one file
        return next

    next.path.unlink()
    next.path.parent.rmdir()
    log.info(f"Deleting Duplicate Release {next.name}")
    return this


def images(component: Release) -> Generator[str, None, None]:
    """Yield all images from each release."""
    with Path(component.path).open() as fp:
        for line in fp:
            m = IMG_RE.match(line)
            if m:
                yield m.groups()[0]


def mirror_image(images: Set[str], registry: Registry):
    """Synchronize all source images to target registry, only pushing changed layers."""
    sync_config = SyncConfig(
        version=1,
        creds=[registry.creds],
        sync=[sync_asset(image, registry) for image in images],
    )
    with NamedTemporaryFile(mode="w") as tmpfile:
        yaml.safe_dump(sync_config, tmpfile)
        proc = subprocess.Popen(
            ["./regsync", "once", "-c", tmpfile.name, "-v", "debug"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
        while proc.returncode is None:
            for line in proc.stdout:
                print(line.strip())
            proc.poll()


def get_argparser():
    """Build the argparse instance."""
    parser = argparse.ArgumentParser(
        description="Update from upstream releases.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--registry",
        default=None,
        type=str,
        nargs=4,
        help="Registry to which images should be mirrored.\n\n"
        "example\n"
        "  --registry my.registry:5000 path username password-file\n"
        "\n"
        "Mirroring depends on binary regsync "
        "(https://github.com/regclient/regclient/releases)\n"
        "and that it is available in the current working directory",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=list(SOURCES.keys()),
        choices=SOURCES.keys(),
        type=str,
        help="Which manifest sources to be updated.\n\n"
        "example\n"
        "  --source cloud_provider\n"
        "\n",
    )
    return parser


class UpdateError(Exception):
    """Represents an error performing the update."""


if __name__ == "__main__":
    try:
        args = get_argparser().parse_args()
        registry = Registry(*args.registry) if args.registry else None
        image_set = set()
        for source in args.sources:
            version, source_images = main(source, registry)
            Path(FILEDIR, source, "version").write_text(f"{version}\n")
            print(f"source: {source} latest={version}")
            image_set |= source_images
        print("images:")
        for image in sorted(image_set):
            print(image)
    except UpdateError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
