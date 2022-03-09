# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation logic for the vSphere CPI operator charm."""

import logging
from dataclasses import dataclass
from pathlib import Path
from random import choices
from string import hexdigits

import jsonschema
import yaml
from charms.vsphere_cloud_provider_operator.v0.lightkube_helpers import LightKubeHelpers
from charms.vsphere_cloud_provider_operator.v0.vsphere_integration import (
    VsphereIntegrationRequires,
)
from lightkube.models.apps_v1 import DaemonSet
from lightkube.resources.core_v1 import Secret
from ops.charm import RelationBrokenEvent
from ops.framework import Object
from ops.model import Relation

from templates import TemplateEngine

log = logging.getLogger(__name__)


class CharmBackend(Object):
    """Implementation logic for the vSphere CPI operator charm."""

    manifests = Path("upstream", "manifests")

    def __init__(self, charm):
        super().__init__(charm, "backend")
        self.charm = charm
        self.lk_helpers = LightKubeHelpers(charm)

    @property
    def integrator(self) -> VsphereIntegrationRequires:
        """Shortcut to `self.charm.integrator`."""
        return self.charm.integrator

    @property
    def external_cloud_provider(self) -> Relation:
        """Shortcut to `self.charm.external_cloud_provider`."""
        return self.charm.external_cloud_provider

    @property
    def config(self):
        """Shortcut to `self.charm.config`."""
        return self.charm.config

    @property
    def app(self):
        """Shortcut to `self.charm.app`."""
        return self.charm.app

    def apply(self):
        """Apply all of the upstream manifests."""
        for manifest in self.manifests.glob("**/*.yaml"):
            if "secret" in manifest.name:
                # The upstream secret contains dummy data, so skip it.
                continue
            self.lk_helpers.apply_manifest(manifest)

    def restart(self):
        """Restart the VCCM DaemonSet."""
        daemonsets = self.lk_helpers.client.list(
            DaemonSet,
            namespace="kube-system",
            labels={"app.juju.is/created-by": f"{self.app.name}"},
            fields={"metadata.name": "vsphere-cloud-controller-manager"},
        )
        if not daemonsets:
            log.error("CCM pod not found to restart")
            return
        ds = daemonsets[0]
        # No "rollout restart" command available, so we patch the DS with
        # an annotation w/ a random value to force a restart.
        ds.metadata.annotations["restart"] = "".join(choices(hexdigits, k=4))
        self.lk_helpers.client.patch(DaemonSet, "vsphere-cloud-controller-manager", ds)

    def remove(self):
        """Remove all of the components from the upstream manifests."""
        for manifest in self.manifests.glob("**/*.yaml"):
            self.lk_helpers.delete_manifest(manifest, ignore_unauthorized=True)

    def build_cloud_config(self):
        """Build a set of cloud config params based on config and relation data."""
        return CharmConfig.load(self)

    def apply_cloud_config(self, cloud_config):
        """Create or update the `cloud-config` Secret resource."""
        config = cloud_config.properties
        templates = TemplateEngine(
            juju_app=self.app.name,
            control_node_selector=config["control-node-selector"],
            server=config["server"],
            username=config["username"],
            password=config["password"],
            datacenter=config["datacenter"],
            image=self.config.get("image"),
        )
        self.lk_helpers.apply_resources(templates.secret.lightkube)

    def delete_cloud_config(self):
        """Remove the `cloud-config` Secret resource, if we created it."""
        secrets = self.lk_helpers.client.list(
            Secret,
            namespace="kube-control",
            labels={"app.juju.is/created-by": f"{self.app.name}"},
            fields={"metadata.name": "cloud-config"},
        )
        if not secrets:
            return
        self.lk_helpers.delete_resource(
            Secret,
            name="cloud-config",
            namespace="kube-system",
        )


@dataclass
class CharmConfig:
    """Representation of the required charm configuration."""

    properties: dict
    backend: CharmBackend
    _schema = yaml.safe_load(Path("schemas", "config-schema.yaml").read_text())

    @classmethod
    def load(cls, backend: CharmBackend):
        """Creates a CharmConfig object from relation and configuration data."""
        cloud_config = {
            "server": backend.integrator.vsphere_ip,
            "username": backend.integrator.user,
            "password": backend.integrator.password,
            "datacenter": backend.integrator.datacenter,
            **{k: backend.config[k] for k in cls._schema["properties"] if backend.config.get(k)},
        }

        if not cloud_config.get("control-node-selector") and backend.external_cloud_provider:
            cloud_config[
                "control-node-selector"
            ] = f"juju-application={backend.external_cloud_provider.app.name}"

        # Clear out empty / null values.
        for key, value in {
            **cloud_config,
        }.items():
            if value == "" or value is None:
                del cloud_config[key]
        return cls._transform_cloud_config(cloud_config, backend)

    @classmethod
    def _transform_cloud_config(cls, cloud_config, backend: CharmBackend):
        value = cloud_config.get("control-node-selector")
        if value is not None:
            updated = cloud_config["control-node-selector"] = {}
            for label in value.split(" "):
                try:
                    key, value = label.split("=")
                except ValueError:
                    log.warning(f"Skipping invalid label {label}")
                else:
                    updated[key] = value
        return cls(cloud_config, backend)

    def evaluate_relation(self, event):
        """Determine if configuration is missing by a specific relation."""
        props = ["server", "username", "password", "datacenter"]
        no_relation = not self.backend.integrator.relation or (
            isinstance(event, RelationBrokenEvent)
            and event.relation is self.backend.integrator.relation
        )
        if any(prop not in self.properties for prop in props):
            if no_relation:
                return "Missing required config or integrator"
            return "Waiting for integrator"

        props = ["control-node-selector"]
        no_relation = not self.backend.external_cloud_provider or (
            isinstance(event, RelationBrokenEvent)
            and event.relation is self.backend.external_cloud_provider
        )
        if any(prop not in self.properties for prop in props):
            if no_relation:
                return "Missing required config or external-cloud-provider"
            return "Waiting for external-cloud-provider"

    @property
    def validate_cloud_config(self):
        """Validate the given cloud config params and return any error."""
        try:
            jsonschema.validate(self.properties, self._schema)
        except jsonschema.ValidationError as e:
            log.exception("Failed to validate cloud config params")
            return e.message
        return None
