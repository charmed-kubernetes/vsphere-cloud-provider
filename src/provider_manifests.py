# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere specific details of the kubernetes manifests."""
import json
import logging
from hashlib import md5
from typing import Dict, Optional

import yaml

from manifests import CharmLabel, ConfigRegistry, Manifests, Patch

log = logging.getLogger(__file__)
SECRET_NAME = "vsphere-cloud-secret"


class ApplySecrets(Patch):
    """Update the secret as a patch since the manifests includes a default."""

    def __call__(self, obj):
        """Update the secrets object in the deployment."""
        if not (obj.kind == "Secret" and obj.metadata.name == SECRET_NAME):
            return
        secret = [self.manifests.config.get(k) for k in ("username", "password", "server")]
        if any(s is None for s in secret):
            log.error("secret data item is None")
            return
        user, passwd, server = secret
        log.info(f"Applying provider secret data for server {server}")
        obj.stringData = {f"{server}.username": user, f"{server}.password": passwd}


class ApplyConfigMap(Patch):
    """Update the ConfigMap as a patch since the manifests includes a default."""

    def __call__(self, obj):
        """Update the ConfigMap object in the deployment."""
        if not (obj.kind == "ConfigMap" and obj.metadata.name == "vsphere-cloud-config"):
            return
        config = [self.manifests.config.get(k) for k in ("server", "datacenter")]
        if any(c is None for c in config):
            log.error("provider ConfigMap server or datacenter is undefined")
            return
        server, datacenter = config
        log.info(f"Applying provider ConfigMap Data for vcenter {datacenter}")
        vsphere_conf = {
            "global": dict(
                port=443, insecureFlag=True, secretName=SECRET_NAME, secretNamespace="kube-system"
            ),
            "vcenter": {
                datacenter: dict(
                    server=server, secretName=SECRET_NAME, secretNamespace="kube-system"
                )
            },
        }
        obj.data["vsphere.conf"] = yaml.safe_dump(vsphere_conf)


class ApplyControlNodeSelector(Patch):
    """Update the Deployment object to reference juju supplied node selector."""

    def __call__(self, obj):
        """Update the DaemonSet object in the deployment."""
        if not (
            obj.kind == "DaemonSet" and obj.metadata.name == "vsphere-cloud-controller-manager"
        ):
            return
        node_selector = self.manifests.config.get("control-node-selector")
        if not isinstance(node_selector, dict):
            log.error(
                f"provider control-node-selector was an unexpected type: {type(node_selector)}"
            )
            return
        obj.spec.template.spec.nodeSelector = node_selector
        node_selector_text = " ".join('{0}: "{1}"'.format(*t) for t in node_selector.items())
        log.info(f"Applying provider Control Node Selector as {node_selector_text}")


class VsphereProviderManifests(Manifests):
    """Deployment Specific details for the vsphere-cloud-provider."""

    def __init__(self, charm_name, charm_config, integrator, control_plane, kube_control):
        manipulations = [
            CharmLabel(self),
            ConfigRegistry(self),
            ApplySecrets(self),
            ApplyConfigMap(self),
            ApplyControlNodeSelector(self),
        ]
        super().__init__(charm_name, "upstream/cloud_provider", manipulations=manipulations)
        self.charm_config = charm_config
        self.integrator = integrator
        self.control_plane = control_plane
        self.kube_control = kube_control

    @property
    def config(self) -> Dict:
        """Returns current config available from charm config and joined relations."""
        config = {}
        if self.integrator.is_ready:
            config.update(
                {
                    "server": self.integrator.vsphere_ip,
                    "username": self.integrator.user,
                    "password": self.integrator.password,
                    "datacenter": self.integrator.datacenter,
                }
            )
        if self.kube_control.is_ready:
            config["image-registry"] = self.kube_control.registry_location

        if self.control_plane:
            config["control-node-selector"] = {"juju-application": self.control_plane.app.name}

        config.update(**self.charm_config.available_data)

        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]

        config["release"] = config.pop("provider-release", None)

        return config

    def hash(self) -> int:
        """Calculate a hash of the current configuration."""
        return int(md5(json.dumps(self.config, sort_keys=True).encode("utf8")).hexdigest(), 16)

    def evaluate(self) -> Optional[str]:
        """Determine if manifest_config can be applied to manifests."""
        props = ["server", "username", "password", "datacenter", "control-node-selector"]
        for prop in props:
            value = self.config.get(prop)
            if not value:
                return f"Provider manifests waiting for definition of {prop}"
        return None
