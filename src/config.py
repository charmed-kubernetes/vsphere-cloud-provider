# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Config Management for the vSphere CPI operator charm."""

import logging
from pathlib import Path

import jsonschema
import yaml
from ops.charm import RelationBrokenEvent

log = logging.getLogger(__name__)


class CharmConfig:
    """Representation of the required charm configuration."""

    _schema = yaml.safe_load(Path("schemas", "config-schema.yaml").read_text("utf-8"))

    def __init__(self, charm):
        """Creates a CharmConfig object from relation and configuration data."""
        cloud_config = {
            "server": charm.integrator.vsphere_ip,
            "username": charm.integrator.user,
            "password": charm.integrator.password,
            "datacenter": charm.integrator.datacenter,
            **{k: charm.config[k] for k in self._schema["properties"] if charm.config.get(k)},
        }

        if not cloud_config.get("control-node-selector") and charm.control_plane_relation:
            cloud_config[
                "control-node-selector"
            ] = f"juju-application={charm.control_plane_relation.app.name}"

        # Clear out empty / null values.
        for key, value in dict(**cloud_config).items():
            if value == "" or value is None:
                del cloud_config[key]

        value = cloud_config.get("control-node-selector")
        if value is not None:
            updated = cloud_config["control-node-selector"] = {}
            for label in value.split(" "):
                try:
                    key, value = label.split("=")
                except ValueError:
                    log.warning("Skipping invalid label %s", label)
                else:
                    updated[key] = value

        self.properties = cloud_config
        self.charm = charm

    def evaluate_relation(self, event):
        """Determine if configuration is missing by a specific relation."""
        props = ["server", "username", "password", "datacenter"]
        no_relation = not self.charm.integrator.relation or (
            isinstance(event, RelationBrokenEvent)
            and event.relation is self.charm.integrator.relation
        )
        if any(prop not in self.properties for prop in props):
            if no_relation:
                return "Missing required config or integrator"
            return "Waiting for integrator"

        props = ["control-node-selector"]
        no_relation = not self.charm.control_plane_relation or (
            isinstance(event, RelationBrokenEvent)
            and event.relation is self.charm.control_plane_relation
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
        except jsonschema.ValidationError as ex:
            log.exception("Failed to validate cloud config params")
            return str(ex)
        return None
