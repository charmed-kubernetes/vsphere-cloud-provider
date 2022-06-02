# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation logic for the vSphere CPI operator charm."""

import logging
from collections import defaultdict
from pathlib import Path
from random import choices
from string import hexdigits

from backports.cached_property import cached_property
from lightkube.resources.apps_v1 import DaemonSet
from ops.framework import Object

from lightkube_helpers import LightKubeHelpers
from templates import TemplateEngine

log = logging.getLogger(__name__)


class CharmBackend(Object):
    """Implementation logic for the vSphere CPI operator charm."""

    manifests = Path("upstream", "manifests")

    def __init__(self, charm):
        super().__init__(charm, "backend")
        self.charm = charm

    @cached_property
    def lk_helpers(self) -> LightKubeHelpers:
        """Lazy evaluation to build LightKubeHelpers when first necessary."""
        return LightKubeHelpers(self.charm.app.name)

    @property
    def app(self):
        """Shortcut to `self.charm.app`."""
        return self.charm.app

    def _templates(self, config):
        return TemplateEngine(
            juju_app=self.app.name,
            control_node_selector=config.get("control-node-selector") or {},
            server=config["server"],
            username=config["username"],
            password=config["password"],
            datacenter=config["datacenter"],
            image=self.charm.config.get("image"),
        )

    def apply_statics(self):
        """Apply templates which don't depend on relation or config."""
        templates = self._templates(defaultdict(str))
        log.info("Applying static objects from templates: %s", templates)
        self.lk_helpers.apply_resources(
            templates.role_bindings.lightkube
            + templates.roles.lightkube
            + templates.service.lightkube
        )

    def restart(self):
        """Restart the VCCM DaemonSet."""
        daemonsets = list(
            self.lk_helpers.client.list(
                DaemonSet,
                namespace="kube-system",
                labels={"app.juju.is/created-by": f"{self.app.name}"},
                fields={"metadata.name": "vsphere-cloud-controller-manager"},
            )
        )
        if not daemonsets:
            log.error("CCM pod not found to restart")
            return
        ds = daemonsets[0]
        # No "rollout restart" command available, so we patch the DS with
        # an annotation w/ a random value to force a restart.
        ds.metadata.annotations["restart"] = "".join(choices(hexdigits, k=4))
        self.lk_helpers.client.patch(
            DaemonSet,
            "vsphere-cloud-controller-manager",
            ds,
            namespace=ds.metadata.namespace,
        )

    def remove(self):
        """Remove all the static components."""
        templates = self._templates(defaultdict(str))
        log.info("Removing static objects from templates: %s", templates)
        self.lk_helpers.delete_resources(
            templates.role_bindings.lightkube
            + templates.roles.lightkube
            + templates.service.lightkube
            + templates.secret.lightkube
            + templates.config_map.lightkube
            + templates.daemonset.lightkube,
            ignore_not_found=True,
            ignore_unauthorized=True,
        )

    def apply_cloud_config(self, cloud_config):
        """Create or update the `cloud-config` resources."""
        config = cloud_config.properties
        templates = self._templates(config)
        log.info("Applying cloud-config from templates: %s", templates)
        self.lk_helpers.apply_resources(
            templates.secret.lightkube
            + templates.config_map.lightkube
            + templates.daemonset.lightkube
        )

    def delete_cloud_config(self):
        """Remove the `cloud-config` resources, if we created it."""
        templates = self._templates(defaultdict(str))
        log.info("Removing cloud_config from templates: %s", templates)
        resources = (
            templates.secret.lightkube
            + templates.config_map.lightkube
            + templates.daemonset.lightkube
        )

        for resource in resources:
            obj = self.lk_helpers.client.list(
                type(resource),
                namespace=resource.metadata.namespace,
                labels={"app.juju.is/created-by": f"{self.app.name}"},
                fields={"metadata.name": resource.metadata.name},
            )
            if not obj:
                continue

            self.lk_helpers.delete_resource(
                type(resource), namespace=resource.metadata.namespace, name=resource.metadata.name
            )
