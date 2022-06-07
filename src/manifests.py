# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Base class of to apply kubernetes manifests from files."""

import abc
import logging
import os
from collections import defaultdict, namedtuple
from itertools import islice
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Union

import yaml
from backports.cached_property import cached_property
from lightkube import Client, codecs
from lightkube.core.client import GlobalResource, NamespacedResource
from lightkube.core.exceptions import ApiError

log = logging.getLogger(__file__)
AnyResource = Union[NamespacedResource, GlobalResource]


class _NamespaceKind(namedtuple("_NamespaceKind", "kind, namespace")):
    def __str__(self):
        if self.namespace:
            return "/".join(self)
        return self.kind


class _HashableResource:
    def __init__(self, manifest, rsc):
        self.manifest = manifest
        self.rsc = rsc

    def uniq(self):
        kind = type(self.rsc).__name__
        ns = self.metadata.namespace
        name = self.metadata.name
        return kind, ns, name

    def __str__(self):
        return "/".join(filter(None, self.uniq()))

    def __hash__(self):
        return hash(self.uniq())

    def __eq__(self, other):
        return other.uniq() == self.uniq()

    def __getattr__(self, item):
        return getattr(self.rsc, item)


class Manipulation:
    """Class used to support charm deviations from the manifests."""

    def __init__(self, manifests: "Manifests") -> None:
        self.manifests = manifests


class Patch(Manipulation):
    """Class used to define how to patch an existing object in the manifests."""

    def __call__(self, obj: Optional[Dict]) -> None:
        """Method called to optionally update the object before application."""
        ...


class Addition(Manipulation):
    """Class used to define objects to add to the original manifests."""

    def __call__(self) -> Optional[Dict]:
        """Method called to optionally create an object."""
        ...


class CreateNamespace(Addition):
    """Class used to create additional namespace before apply manifests."""

    def __init__(self, manifests: "Manifests", namespace="") -> None:
        super().__init__(manifests)
        self.namespace = namespace

    def __call__(self):
        """Create the default namespace if available."""
        which_ns = self.namespace or self.manifests.namespace
        if which_ns:
            return dict(
                apiVersion="v1",
                kind="Namespace",
                metadata=dict(name=which_ns),
            )


class ApplyLabel(Patch):
    """Ensure every manifest item is labeled with charm name."""

    def __call__(self, obj):
        """Adds charm-name label to obj."""
        obj["metadata"].setdefault("labels", {})
        obj["metadata"].setdefault("name", "")
        obj["metadata"]["labels"][self.manifests.charm_name] = "true"


class ApplyRegistry(Patch):
    """Applies image registry to the manifest."""

    def __call__(self, obj):
        """Uses the image-registry config for the manifest and updates all container images."""
        registry = self.manifests.config.get("image-registry")
        if not registry:
            return
        spec = obj.get("spec") or {}
        template = spec and spec.get("template") or {}
        inner_spec = template and template.get("spec") or {}
        containers = inner_spec and inner_spec.get("containers") or {}
        for container in containers:
            full_image = container.get("image")
            if full_image:
                _, image = full_image.split("/", 1)
                new_full_image = f"{registry}/{image}"
                container["image"] = new_full_image
                log.info(f"Replacing Image: {full_image} with {new_full_image}")


class Manifests(abc.ABC):
    """Class used to apply manifest files from a release directory."""

    def __init__(
        self, charm_name: str, base_path: os.PathLike, manipulations=None, default_namespace=""
    ):
        self.namespace = default_namespace
        self.charm_name = charm_name
        self.base_path = Path(base_path)
        self.manipulations = manipulations or []

    @cached_property
    def client(self) -> Client:
        """Lazy evaluation of the lightkube client."""
        return Client(namespace=self.namespace, field_manager=self.charm_name)

    @abc.abstractproperty
    def config(self) -> Dict:
        """Retrieve the current available config to use during manifest building."""
        ...

    @cached_property
    def manifest_path(self) -> Path:
        """Retrieve the path where the versioned manifests exist."""
        return self.base_path / "manifests"

    @cached_property
    def releases(self) -> List[str]:
        """List all possible releases supported by the charm sorted by latest release first."""
        return sorted(
            [manifests.parent.name for manifests in self.manifest_path.glob("*/*.yaml")],
            key=lambda name: tuple(map(int, name[1:].split("."))),
            reverse=True,
        )  # sort numerically

    @property
    def latest_release(self) -> str:
        """Lookup the latest release supported by the charm."""
        return (self.base_path / "version").read_text(encoding="utf-8").strip()

    @property
    def current_release(self) -> str:
        """Determine the current release from charm config."""
        return self.config.get("release") or self.latest_release

    @cached_property
    def resources(self) -> Mapping[_NamespaceKind, Set[_HashableResource]]:
        """All component resource sets subdivided by kind and namespace."""
        result: Mapping[_NamespaceKind, Set[_HashableResource]] = defaultdict(set)
        ver = self.current_release
        for manifest in (self.manifest_path / ver).glob("*.yaml"):
            for obj in codecs.load_all_yaml(manifest.read_text()):
                kind_ns = _NamespaceKind(obj.kind, obj.metadata.namespace)
                result[kind_ns].add(_HashableResource(self, obj))
        return result

    def status(self) -> Set[_HashableResource]:
        """Returns all objects which have a `.status.conditions` attribute."""
        objects = [
            self.client.get(
                type(obj.rsc),
                obj.metadata.name,
                namespace=obj.metadata.namespace,
            )
            for resources in self.resources.values()
            for obj in resources
        ]
        return set(
            _HashableResource(self, obj)
            for obj in objects
            if hasattr(obj, "status") and obj.status.conditions
        )

    def expected_resources(self) -> Mapping[_NamespaceKind, Set[_HashableResource]]:
        """All currently installed resources expected by this charm."""
        result: Mapping[_NamespaceKind, Set[_HashableResource]] = defaultdict(set)
        for key, resources in self.resources.items():
            for obj in resources:
                result[key].add(
                    _HashableResource(
                        self,
                        self.client.get(
                            type(obj.rsc),
                            obj.metadata.name,
                            namespace=obj.metadata.namespace,
                        ),
                    )
                )
        return result

    def active_resources(self) -> Mapping[_NamespaceKind, Set[_HashableResource]]:
        """All currently installed resources ever labeled by this charm."""
        return {
            key: set(
                _HashableResource(self, rsc)
                for rsc in self.client.list(
                    type(obj.rsc),
                    namespace=obj.metadata.namespace,
                    labels={self.charm_name: "true"},
                )
            )
            for key, resources in self.resources.items()
            for obj in islice(resources, 1)  # take the first element if it exists
        }

    def apply_manifests(self):
        """Apply all manifest files from the current release."""
        ver = self.current_release
        for component in (self.manifest_path / ver).glob("*.yaml"):
            self.apply_manifest(component)

    def delete_manifests(self, **kwargs):
        """Delete all manifests associated with the current resources."""
        for resources in self.resources.values():
            self.delete_resources(*resources, **kwargs)

    def apply_manifest(self, filepath: Path):
        """Read file object and apply all objects from the manifest."""
        text = self._modify(filepath.read_text())
        for obj in codecs.load_all_yaml(text):
            name = obj.metadata.name
            namespace = obj.metadata.namespace
            log.info(f"Adding {obj.kind}/{name}" + (f" to {namespace}" if namespace else ""))
            self.client.apply(obj, name, force=True)

    def _modify(self, content: str) -> str:
        data = [_ for _ in yaml.safe_load_all(content) if _]

        def patch(obj):
            for manipulate in self.manipulations:
                if isinstance(manipulate, Patch):
                    manipulate(obj)

        for manipulate in reversed(self.manipulations):
            if isinstance(manipulate, Addition):
                data.insert(0, manipulate())

        for part in data:
            if part["kind"] == "List":
                for item in part["items"]:
                    patch(item)
            else:
                patch(part)
        return yaml.safe_dump_all(data)

    def delete_resources(
        self,
        *resources: _HashableResource,
        namespace: Optional[str] = None,
        ignore_not_found: bool = False,
        ignore_unauthorized: bool = False,
    ):
        """Delete named resources."""
        for obj in resources:
            try:
                namespace = obj.metadata.namespace or namespace
                log.info(f"Deleting {obj}")
                self.client.delete(type(obj.rsc), obj.metadata.name, namespace=namespace)
            except ApiError as err:
                if err.status.message is not None:
                    err_lower = err.status.message.lower()
                    if "not found" in err_lower and ignore_not_found:
                        log.warning(f"Ignoring not found error: {err.status.message}")
                    elif "(unauthorized)" in err_lower and ignore_unauthorized:
                        # Ignore error from https://bugs.launchpad.net/juju/+bug/1941655
                        log.warning(f"Unauthorized error ignored: {err.status.message}")
                    else:
                        log.exception(
                            "ApiError encountered while attempting to delete resource: "
                            + err.status.message
                        )
                        raise
                else:
                    log.exception("ApiError encountered while attempting to delete resource.")
                    raise

    delete_resource = delete_resources
