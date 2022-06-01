# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere-integration interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import base64
import json
import logging
from backports.cached_property import cached_property
from os import PathLike
from pathlib import Path
from typing import Mapping, Optional

import jsonschema
import yaml
from ops.charm import RelationBrokenEvent
from ops.framework import Object

log = logging.getLogger(__name__)


class KubeControlRequires(Object):
    """Requires side of kube-control relation."""

    LIMIT = 1
    SCHEMA = {
        "type": "object",
        "properties": {
            "api-endpoints": dict(
                type="array", json=True, items=dict(type="string", format="uri")
            ),
            "cluster-tag": dict(type="string"),
            "cohort-keys": dict(
                type="object", json=True, additionalProperties=dict(type="string")
            ),
            "creds": dict(
                type="object",
                json=True,
                additionalProperties=dict(
                    type="object",
                    properties=dict(
                        client_token=dict(type="string"),
                        kubelet_token=dict(type="string"),
                        proxy_token=dict(type="string"),
                        scope=dict(type="string"),
                    ),
                    required=["client_token", "kubelet_token", "proxy_token", "scope"],
                ),
            ),
            "default-cni": dict(type="string", json=True),
            "domain": dict(type="string"),
            "enable-kube-dns": dict(type="boolean", json=True),
            "has-xcp": dict(type="boolean", json=True),
            "port": dict(type="integer", json=True),
            "registry-location": dict(type="string"),
            "sdn-ip": dict(type="string", format="ipv4"),
        },
        "required": [
            "api-endpoints",
            "cluster-tag",
            "creds",
            "default-cni",
            "domain",
            "enable-kube-dns",
            "has-xcp",
            "port",
            "sdn-ip",
        ],
    }
    IGNORE_FIELDS = {
        "egress-subnets",
        "ingress-address",
        "private-address",
    }

    def __init__(self, charm, endpoint="kube-control"):
        super().__init__(charm, f"relation-{endpoint}")
        self.charm = charm
        self.endpoint = endpoint

    @cached_property
    def relation(self):
        """The relation to the integrator, or None."""
        return self.model.get_relation(self.endpoint)

    @cached_property
    def _data(self):
        if not (self.relation and self.relation.units):
            return {}
        raw_data = self.relation.data[list(self.relation.units)[0]]
        data = {}
        for field, raw_value in raw_data.items():
            if field in self.IGNORE_FIELDS or not raw_value:
                continue
            if field not in self.SCHEMA["properties"]:
                continue
            json_parse = self.SCHEMA["properties"][field].get("json")
            if json_parse:
                if self.SCHEMA["properties"][field].get("type") == "boolean":
                    raw_value = raw_value.lower()
                try:
                    data[field] = json.loads(raw_value)
                except json.JSONDecodeError as e:
                    log.error(f"Failed to decode relation data in {field}: {e}")
            else:
                data[field] = raw_value
        return data

    def evaluate_relation(self, event) -> Optional[str]:
        """Determine if relation is ready."""
        no_relation = not self.relation or (
            isinstance(event, RelationBrokenEvent) and event.relation is self.relation
        )
        if not self.is_ready:
            if no_relation:
                return f"Missing required {self.endpoint} relation"
            return f"Waiting for {self.endpoint} relation"

    @property
    def is_ready(self):
        """Whether the request for this instance has been completed."""
        try:
            jsonschema.validate(self._data, self.SCHEMA)
        except jsonschema.ValidationError:
            log.error(f"{self.endpoint} relation data not yet valid.")
            return False
        return True

    def _value(self, key):
        if not self._data:
            return None
        return self._data.get(key)

    def create_kubeconfig(self, ca: PathLike, kubeconfig: PathLike, user: str, k8s_user: str):
        """Write kubeconfig based on available creds."""
        creds = self.get_auth_credentials(k8s_user)

        cluster = "juju-cluster"
        context = "juju-context"
        server = self.api_endpoints[0]
        token = creds["client_token"]
        ca_b64 = base64.b64encode(Path(ca).read_bytes()).decode("utf-8")

        # Create the config file with the address of the control-plane server.
        config_contents = {
            "apiVersion": "v1",
            "kind": "Config",
            "preferences": {},
            "clusters": [
                {
                    "cluster": {
                        "certificate-authority-data": ca_b64,
                        "server": server,
                    },
                    "name": cluster,
                }
            ],
            "contexts": [{"context": {"cluster": cluster, "user": user}, "name": context}],
            "users": [{"name": user, "user": {"token": token}}],
            "current-context": context,
        }
        old_kubeconfig = Path(kubeconfig)
        new_kubeconfig = Path(f"{kubeconfig}.new")
        new_kubeconfig.parent.mkdir(exist_ok=True, mode=0o750)
        new_kubeconfig.write_text(yaml.safe_dump(config_contents))
        new_kubeconfig.chmod(mode=0o600)

        if old_kubeconfig.exists():
            changed = new_kubeconfig.read_text() != old_kubeconfig.read_text()
        else:
            changed = True
        if changed:
            new_kubeconfig.rename(old_kubeconfig)

    @property
    def api_endpoints(self):
        """The api-endpoints value."""
        return self._value("api-endpoints")

    @property
    def cluster_tag(self):
        """The cluster-tag value."""
        return self._value("cluster-tag")

    @property
    def cohort_keys(self):
        """The cohort-keys value."""
        return self._value("cohort-keys")

    @property
    def creds(self):
        """The creds value."""
        return self._value("creds")

    @property
    def default_cni(self):
        """The default-cni value."""
        return self._value("default-cni")

    @property
    def domain(self):
        """The domain value."""
        return self._value("domain")

    @property
    def enable_kube_dns(self):
        """The enable-kube-dns value."""
        return self._value("enable-kube-dns")

    @property
    def has_xcp(self):
        """The has-xcp value."""
        return self._value("has-xcp")

    @property
    def port(self):
        """The port value."""
        return self._value("port")

    @property
    def registry_location(self):
        """The registry-location value."""
        return self._value("registry-location")

    @property
    def sdn_ip(self):
        """The sdn_ip value."""
        return self._value("sdn-ip")

    def set_auth_request(self, user, group="system:nodes"):
        """Notify contol-plane that we are requesting auth.

        Also, use this hostname for the kubelet system account.

        @params user   - user requesting authentication
        @params groups - Determines the level of eleveted privileges of the
                         requested user.
                         Can be overridden to request sudo level access on the
                         cluster via changing to system:masters.  #wokeignore:rule=master
        """
        if self.relation:
            self.relation.data[self.charm.unit].update(dict(kubelet_user=user, auth_group=group))

    def set_gpu(self, enabled=True):
        """Tell the control-plane that we're gpu-enabled (or not)."""
        log("Setting gpu={} on kube-control relation".format(enabled))
        for relation in self.relation:
            relation.data.update({"gpu": enabled})

    def get_auth_credentials(self, user) -> Optional[Mapping[str, str]]:
        """Return the authentication credentials."""
        if not self._data:
            return None

        if user in self.creds:
            return {
                "user": user,
                "kubelet_token": self.creds[user]["kubelet_token"],
                "proxy_token": self.creds[user]["proxy_token"],
                "client_token": self.creds[user]["client_token"],
            }
        return None

    def get_dns(self):
        """Return DNS info provided by the control-plane."""
        return {
            "port": self.port,
            "domain": self.domain,
            "sdn-ip": self.sdn_ip,
            "enable-kube-dns": self.enable_kube_dns,
        }

    def dns_ready(self):
        """Return True if we have all DNS info from the control-plane."""
        keys = ["port", "domain", "sdn-ip", "enable-kube-dns"]
        dns_info = self.get_dns()
        return set(dns_info.keys()) == set(keys) and dns_info["enable-kube-dns"] is not None
