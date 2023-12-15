# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere specific details of the kubernetes manifests."""
import base64
import logging
import pickle
from hashlib import md5
from typing import Dict, Optional

from lightkube.codecs import AnyResource, from_dict
from lightkube.models.core_v1 import Toleration
from ops.manifests import (
    Addition,
    ConfigRegistry,
    CreateNamespace,
    ManifestLabel,
    Manifests,
    Patch,
)

log = logging.getLogger(__file__)
NAMESPACE = "vmware-system-csi"
SECRET_NAME = "vsphere-config-secret"
SECRET_DATA = "csi-vsphere.conf"
STORAGE_CLASS_NAME = "csi-vsphere-{type}"


class UpdateStorageDeployment(Patch):
    """Update the Deployment object to reference juju supplied node selector and replica."""

    def __call__(self, obj):
        """Apply control node selector and replica count to the deployment."""
        if not (obj.kind == "Deployment" and obj.metadata.name == "vsphere-csi-controller"):
            return
        node_selector = self.manifests.config.get("control-node-selector")
        if not isinstance(node_selector, dict):
            log.error(
                f"storage control-node-selector was an unexpected type: {type(node_selector)}"
            )
            return

        obj.spec.template.spec.nodeSelector = node_selector
        node_selector_text = " ".join('{0}: "{1}"'.format(*t) for t in node_selector.items())
        log.info(f"Applying storage Control Node Selector as {node_selector_text}")

        replicas = self.manifests.config.get("replicas")
        if not replicas:
            log.warning(f"Using storage default replicas of {obj.spec.replicas}")
        else:
            obj.spec.replicas = replicas
            log.info(f"Setting storage deployment replicas to {replicas}")

        obj.spec.template.spec.tolerations += [
            Toleration(
                key=taint.key,
                value=taint.value,
                effect=taint.effect,
            )
            for taint in self.manifests.config.get("control-node-taints", [])
        ]
        log.info("Adding storage tolerations from control-plane")


class EnableCSIMigration(Patch):
    """Update Internal Features ConfigMap to handle CSIMigration."""

    def __call__(self, obj: AnyResource) -> None:
        """Handle CSIMigation from charm config."""
        if not (
            obj.kind == "ConfigMap"
            and obj.metadata.name == "internal-feature-states.csi.vsphere.vmware.com"
        ):
            return
        data = obj.data
        if not isinstance(data, dict):
            log.error(f"data was an unexpected type: {type(data)}")
            return
        migration = self.manifests.config.get("csi-migration")
        data["csi-migration"] = migration
        log.info(f"Setting CSIMigration to {migration}")


class CreateSecret(Addition):
    """Create secret for the deployment."""

    def __call__(self) -> Optional[AnyResource]:
        """Craft the secrets object for the deployment."""
        obj = from_dict(
            dict(
                apiVersion="v1",
                kind="Secret",
                type="Opaque",
                metadata=dict(name=SECRET_NAME, namespace=NAMESPACE),
                data=dict(),
            )
        )
        secret_config = [
            self.manifests.config.get(k)
            for k in ("username", "password", "server", "datacenter", "model-uuid")
        ]
        if any(s is None for s in secret_config):
            log.error("secret data item is None")
            return obj

        user, passwd, server, datacenter, model_uuid = secret_config
        log.info(f"Creating storage secret data for server {server}")
        data = (
            f"[Global]\n"
            f'cluster-id = "{model_uuid}"\n'
            f"\n"
            f'[VirtualCenter "{server}"]\n'
            f'insecure-flag = "true"\n'
            f'user = "{user}"\n'
            f'password = "{passwd}"\n'
            f'port = "443"\n'
            f'datacenters = "{datacenter}"\n'
        ).encode()
        obj.data[SECRET_DATA] = base64.b64encode(data).decode("utf-8")
        return obj


class CreateStorageClass(Addition):
    """Create vmware storage class."""

    def __init__(self, manifests: "Manifests", sc_type: str):
        super().__init__(manifests)
        self.type = sc_type

    @property
    def _params(self) -> Dict[str, str]:
        parameter_config = self.manifests.config["storage-class-parameters"]
        parameters = {}
        for param in parameter_config.split(","):
            try:
                key, val = param.split("=", 1)
                parameters[key] = val
            except ValueError:
                log.error("Storage class parameter missing '=' separator in '%s'", param)
        return parameters

    def __call__(self) -> Optional[AnyResource]:
        """Craft the storage class object."""
        storage_name = STORAGE_CLASS_NAME.format(type=self.type)
        log.info(f"Creating storage class {storage_name}")
        return from_dict(
            dict(
                apiVersion="storage.k8s.io/v1",
                kind="StorageClass",
                metadata=dict(
                    name=storage_name,
                    annotations={
                        "storageclass.kubernetes.io/is-default-class": "true",
                    },
                ),
                provisioner="csi.vsphere.vmware.com",
                parameters=self._params,
            )
        )


class VsphereStorageManifests(Manifests):
    """Deployment Specific details for the vsphere-cloud-provider."""

    def __init__(
        self,
        charm,
        charm_config,
        integrator,
        kube_control,
        model_uuid: str,
    ):
        manipulations = [
            CreateNamespace(self, NAMESPACE),
            CreateSecret(self),
            ManifestLabel(self),
            ConfigRegistry(self),
            UpdateStorageDeployment(self),
            CreateStorageClass(self, "default"),  # creates csi-vsphere-default
            EnableCSIMigration(self),
        ]
        super().__init__(
            "vsphere-csi-driver",
            charm.model,
            "upstream/cloud_storage",
            manipulations,
        )
        self.charm_config = charm_config
        self.integrator = integrator
        self.kube_control = kube_control
        self.model_uuid = model_uuid

    @property
    def config(self) -> Dict:
        """Returns current config available from charm config and joined relations."""
        config: Dict = {"model-uuid": self.model_uuid}
        if self.integrator.is_ready:
            config["server"] = self.integrator.vsphere_ip
            config["username"] = self.integrator.user
            config["password"] = self.integrator.password
            config["datacenter"] = self.integrator.datacenter

        if self.kube_control.is_ready:
            config["image-registry"] = self.kube_control.get_registry_location()
            config["control-node-taints"] = self.kube_control.get_controller_taints() or [
                Toleration("NoSchedule", "node-role.kubernetes.io/control-plane")
            ]  # by default
            config["control-node-selector"] = {
                label.key: label.value for label in self.kube_control.get_controller_labels()
            } or {"juju-application": self.kube_control.relation.app.name}
            config["replicas"] = len(self.kube_control.relation.units)

        config.update(**self.charm_config.available_data)

        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]

        config["release"] = config.pop("storage-release", None)

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
                return f"Storage manifests waiting for definition of {prop}"
        return None
