# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere specific details of the kubernetes manifests."""
import json
import logging
from hashlib import md5
from typing import Dict

import yaml

from manifests import Manifests

log = logging.getLogger(__file__)
SECRET_NAME = "vsphere-cloud-secret"


class VsphereManifests(Manifests):
    """Deployment Specific details for the vsphere-cloud-provider."""

    def __init__(self, charm_name, charm_config, integrator, control_plane):
        manipulations = [
            self.add_label,
            self.apply_registry,
            self.apply_secrets,
            self.apply_config_map,
            self.apply_control_node_selector,
        ]
        self.charm_config = charm_config
        self.integrator = integrator
        self.control_plane = control_plane
        super().__init__(charm_name, manipulations=manipulations)

    @property
    def config(self) -> Dict:
        """Returns current config available from charm config and joined relations."""
        config = {}
        if self.integrator.is_ready:
            config = {
                "server": self.integrator.vsphere_ip,
                "username": self.integrator.user,
                "password": self.integrator.password,
                "datacenter": self.integrator.datacenter,
            }
        if self.control_plane:
            config["control-node-selector"] = {"juju-application": self.control_plane.app.name}

        config.update(**self.charm_config.available_data)

        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]

        return config

    def hash(self) -> str:
        """Calculate a hash of the current configuration."""
        return md5(json.dumps(self.config, sort_keys=True).encode("utf8")).hexdigest()

    def evaluate(self) -> str:
        """Determine if manifest_config can be applied to manifests."""
        props = ["server", "username", "password", "datacenter", "control-node-selector"]
        for prop in props:
            value = self.config.get(prop)
            if not value:
                return f"Manifests waiting for definition of {prop}"

    @staticmethod
    def _args_or_flags(args_list):
        """Create unique argument dict from value args or flag args."""
        return dict(arg.split("=", 1) if "=" in arg else (arg, None) for arg in args_list)

    def apply_registry(self, obj):
        """Apply image registry to the manifest."""
        registry = self.config.get("image-registry")
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

    def apply_secrets(self, obj):
        """Update the secrets object in the deployment."""
        if not (obj.get("kind") == "Secret" and obj["metadata"]["name"] == SECRET_NAME):
            return
        secret = [self.config.get(k) for k in ("username", "password", "server")]
        if any(s is None for s in secret):
            log.error("secret data item is None")
            return
        user, passwd, server = secret
        log.info(f"Applying Secret Data for server {server}")
        obj["stringData"] = {f"{server}.username": user, f"{server}.password": passwd}

    def apply_config_map(self, obj):
        """Update the ConfigMap object in the deployment."""
        if not (
            obj.get("kind") == "ConfigMap" and obj["metadata"]["name"] == "vsphere-cloud-config"
        ):
            return
        config = [self.config.get(k) for k in ("server", "datacenter")]
        if any(c is None for c in config):
            log.error("server or datacenter is undefined")
            return
        server, datacenter = config
        log.info(f"Applying ConfigMap Data for vcenter {datacenter}")
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
        obj["data"]["vsphere.conf"] = yaml.safe_dump(vsphere_conf)

    def apply_control_node_selector(self, obj):
        """Update the DaemonSet object in the deployment."""
        if not (
            obj.get("kind") == "DaemonSet"
            and obj["metadata"]["name"] == "vsphere-cloud-controller-manager"
        ):
            return
        node_selector = self.config.get("control-node-selector")
        if not isinstance(node_selector, dict):
            log.error(f"control-node-selector was an unexpected type: {type(node_selector)}")
            return
        obj["spec"]["template"]["spec"]["nodeSelector"] = node_selector
        node_selector_text = " ".join('{0}: "{1}"'.format(*t) for t in node_selector.items())
        log.info(f"Applying Control Node Selector as {node_selector_text}")
