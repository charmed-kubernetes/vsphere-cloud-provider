# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Base class of to apply kubernetes manifests from files."""

import abc
import logging
import os
from collections import defaultdict, namedtuple
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Union

import yaml
from backports.cached_property import cached_property
from lightkube import Client, codecs
from lightkube.codecs import AnyResource
from lightkube.core.exceptions import ApiError

log = logging.getLogger(__file__)

PathLike = Union[str, os.PathLike]


class _NamespaceKind(namedtuple("_NamespaceKind", "kind, namespace")):
    def __str__(self):
        if self.namespace:
            return "/".join(self)
        return self.kind


class HashableResource:
    """Wraps a lightkube resource object so it is hashable."""

    def __init__(self, resource: AnyResource):
        self.resource = resource

    def __uniq(self):
        return self.kind, self.namespace, self.name

    @property
    def kind(self) -> str:
        """Return the resource's kind."""
        return self.resource.kind

    @property
    def namespace(self) -> Optional[str]:
        """Return the resource's namespace."""
        return self.resource.metadata.namespace if self.resource.metadata else None

    @property
    def name(self) -> Optional[str]:
        """Return the resource's name."""
        return self.resource.metadata.name if self.resource.metadata else None

    def __str__(self):
        """String version of the unique parts.

        example: 'kind/[namspace/]name'
        """
        return "/".join(filter(None, self.__uniq()))

    def __hash__(self):
        """Returns a hash of the unique parts."""
        return hash(self.__uniq())

    def __eq__(self, other):
        """Comparison only of the unique parts."""
        return isinstance(other, HashableResource) and other.__uniq() == self.__uniq()


class Manipulation:
    """Class used to support charm deviations from the manifests."""

    def __init__(self, manifests: "Manifests") -> None:
        self.manifests = manifests


class Patch(Manipulation):
    """Class used to define how to patch an existing object in the manifests."""

    def __call__(self, obj: AnyResource) -> None:
        """Method called to optionally update the object before application."""
        ...


class Addition(Manipulation):
    """Class used to define objects to add to the original manifests."""

    def __call__(self) -> AnyResource:
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
            return codecs.from_dict(
                dict(
                    apiVersion="v1",
                    kind="Namespace",
                    metadata=dict(name=which_ns),
                )
            )


class CharmLabel(Patch):
    """Ensure every manifest item is labeled with charm name."""

    def __call__(self, obj: AnyResource):
        """Adds charm-name label to obj."""
        obj.metadata.labels = obj.metadata.labels or {}  # ensure object has labels
        obj.metadata.labels[self.manifests.charm_name] = "true"


class ConfigRegistry(Patch):
    """Applies image registry to the manifest."""

    def __call__(self, obj):
        """Uses the image-registry config for the manifest and updates all container images."""
        registry = self.manifests.config.get("image-registry")
        if not registry:
            return
        if obj.kind in ["DaemonSet", "Deployment"]:
            for container in obj.spec.template.spec.containers:
                full_image = container.image
                if full_image:
                    _, image = full_image.split("/", 1)
                    new_full_image = f"{registry}/{image}"
                    container.image = new_full_image
                    log.info(f"Replacing Image: {full_image} with {new_full_image}")


class Manifests(abc.ABC):
    """Class used to apply manifest files from a release directory."""

    def __init__(
        self, charm_name: str, base_path: PathLike, manipulations=None, default_namespace=""
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

    @cached_property
    def latest_release(self) -> str:
        """Lookup the latest release supported by the charm."""
        return (self.base_path / "version").read_text(encoding="utf-8").strip()

    @property
    def current_release(self) -> str:
        """Determine the current release from charm config."""
        return self.config.get("release") or self.latest_release

    @property
    def resources(self) -> Mapping[_NamespaceKind, Set[HashableResource]]:
        """All component resource sets subdivided by kind and namespace."""
        result: Mapping[_NamespaceKind, Set[HashableResource]] = defaultdict(set)
        ver = self.current_release

        # Generated additions
        for manipulate in self.manipulations:
            if isinstance(manipulate, Addition):
                obj = manipulate()
                kind_ns = _NamespaceKind(obj.kind, obj.metadata.namespace)
                result[kind_ns].add(HashableResource(obj))

        # From static manifests
        for manifest in (self.manifest_path / ver).glob("*.yaml"):
            for obj in self._safe_load(manifest):
                kind_ns = _NamespaceKind(obj.kind, obj.metadata.namespace)
                result[kind_ns].add(HashableResource(obj))

        return result

    @lru_cache()
    def _safe_load(self, filepath: Path) -> List[AnyResource]:
        """Read manifest file and parse its content into lightkube objects.

        Lightkube can't properly read manifest files which contain List kinds.
        """
        content = filepath.read_text()
        return [
            codecs.from_dict(item)  # Map to lightkube resources
            for rsc in yaml.safe_load_all(content)  # load content from file
            if rsc  # ignore empty objects
            for item in (rsc["items"] if rsc["kind"] == "List" else [rsc])
        ]

    def status(self) -> Set[HashableResource]:
        """Returns all objects which have a `.status.conditions` attribute."""
        objects = [
            self.client.get(
                type(obj.resource),
                obj.name,
                namespace=obj.namespace,
            )
            for resources in self.resources.values()
            for obj in resources
        ]
        return set(
            HashableResource(obj)
            for obj in objects
            if hasattr(obj, "status") and obj.status.conditions
        )

    def expected_resources(self) -> Mapping[_NamespaceKind, Set[HashableResource]]:
        """All currently installed resources expected by this charm."""
        result: Mapping[_NamespaceKind, Set[HashableResource]] = defaultdict(set)
        for key, resources in self.resources.items():
            for obj in resources:
                result[key].add(
                    HashableResource(
                        self.client.get(
                            type(obj.resource),
                            obj.name,
                            namespace=obj.namespace,
                        ),
                    )
                )
        return result

    def active_resources(self) -> Mapping[_NamespaceKind, Set[HashableResource]]:
        """All currently installed resources ever labeled by this charm."""
        return {
            key: set(
                HashableResource(rsc)
                for rsc in self.client.list(
                    type(obj.resource),
                    namespace=obj.namespace,
                    labels={self.charm_name: "true"},
                )
            )
            for key, resources in self.resources.items()
            for obj in islice(resources, 1)  # take the first element if it exists
        }

    def apply_manifests(self):
        """Apply all manifest files from the current release."""
        resources = (rsc.resource for s in self.resources.values() for rsc in s)

        for rsc in resources:
            for manipulate in self.manipulations:
                if isinstance(manipulate, Patch):
                    manipulate(rsc)
            name = rsc.metadata.name
            namespace = rsc.metadata.namespace
            log.info(f"Applying {rsc.kind}/{name}" + (f" to {namespace}" if namespace else ""))
            self.client.apply(rsc, name, force=True)

    def delete_manifests(self, **kwargs):
        """Delete all manifests associated with the current resources."""
        for resources in self.resources.values():
            self.delete_resources(*resources, **kwargs)

    def delete_resources(
        self,
        *resources: HashableResource,
        namespace: Optional[str] = None,
        ignore_not_found: bool = False,
        ignore_unauthorized: bool = False,
    ):
        """Delete named resources."""
        for obj in resources:
            try:
                namespace = obj.namespace or namespace
                log.info(f"Deleting {obj}")
                self.client.delete(type(obj.resource), obj.name, namespace=namespace)
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
