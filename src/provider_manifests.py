# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere specific details of the kubernetes manifests."""
import logging
import pickle
from hashlib import md5
from typing import Dict, Optional

import yaml
from lightkube.models.core_v1 import Toleration
from ops.manifests import ConfigRegistry, ManifestLabel, Manifests, Patch

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


class UpdateControllerDaemonSet(Patch):
    """Update the Controller DaemonSet object to target juju control plane."""

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

        current_keys = {toleration.key for toleration in obj.spec.template.spec.tolerations}
        missing_tolerations = [
            Toleration(
                key=taint.key,
                value=taint.value,
                effect=taint.effect,
            )
            for taint in self.manifests.config.get("control-node-taints")
            if taint.key not in current_keys
        ]
        obj.spec.template.spec.tolerations += missing_tolerations
        log.info("Adding provider tolerations from control-plane")


class VsphereProviderManifests(Manifests):
    """Deployment Specific details for the vsphere-cloud-provider."""

    def __init__(self, charm, charm_config, integrator, kube_control):
        manipulations = [
            ManifestLabel(self),
            ConfigRegistry(self),
            ApplySecrets(self),
            ApplyConfigMap(self),
            UpdateControllerDaemonSet(self),
        ]
        super().__init__(
            "cloud-provider-vsphere", charm.model, "upstream/cloud_provider", manipulations
        )
        self.charm_config = charm_config
        self.integrator = integrator
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
            config["image-registry"] = self.kube_control.get_registry_location()
            config["control-node-taints"] = self.kube_control.get_controller_taints()
            config["control-node-selector"] = {
                label.key: label.value for label in self.kube_control.get_controller_labels()
            } or {"juju-application": self.kube_control.relation.name}

        config.update(**self.charm_config.available_data)

        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]

        config["release"] = config.pop("provider-release", None)

        return config

    def hash(self) -> int:
        """Calculate a hash of the current configuration."""
        return int(md5(pickle.dumps(self.config)).hexdigest(), 16)

    def evaluate(self) -> Optional[str]:
        """Determine if manifest_config can be applied to manifests."""
        props = ["server", "username", "password", "datacenter", "control-node-selector"]
        for prop in props:
            value = self.config.get(prop)
            if not value:
                return f"Provider manifests waiting for definition of {prop}"
        return None
