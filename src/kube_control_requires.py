# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere-integration interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import json
import logging
from functools import cached_property
from os import PathLike
from pathlib import Path
from typing import List, Optional

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
                return "Missing required kube-control"
            return "Waiting for kube-control"

    def create_kubeconfig(self, path: PathLike, user: str):
        """Write kubeconfig based on available creds."""
        # TODO: DRAGONS BE HERE
        kube_config = {}
        file_path = Path(path)
        file_path.write_text(yaml.safe_dump(kube_config))

    @property
    def is_ready(self):
        """Whether the request for this instance has been completed."""
        try:
            jsonschema.validate(self._data, self.SCHEMA)
        except jsonschema.ValidationError:
            log.error(f"kube-control relation data not yet valid.")
            return False
        return True

    def _value(self, key):
        if not self._data:
            return None
        return self._data.get(key)

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

    def set_auth_request(self, kubelet, group="system:nodes"):
        """
        Tell the master that we are requesting auth, and to use this
        hostname for the kubelet system account.

        Param groups - Determines the level of eleveted privleges of the
        requested user. Can be overridden to request sudo level access on the
        cluster via changing to system:masters.
        """
        if self.relation:
            self.relation.data[self.charm.unit].update(
                dict(kubelet_user=kubelet, auth_group=group)
            )

    def set_gpu(self, enabled=True):
        """
        Tell the master that we're gpu-enabled (or not).
        """
        log("Setting gpu={} on kube-control relation".format(enabled))
        for relation in self.relation:
            relation.data.update({"gpu": enabled})

    def get_auth_credentials(self, user):
        """
        Return the authentication credentials.
        """
        if not self._data:
            return None

        if user in self.creds:
            return {
                "user": user,
                "kubelet_token": self.creds["kubelet_token"],
                "proxy_token": self.creds["proxy_token"],
                "client_token": self.creds["client_token"],
            }
        return None

    def get_dns(self):
        """
        Return DNS info provided by the master.
        """

        return {
            "port": self.port,
            "domain": self.domain,
            "sdn-ip": self.sdn_ip,
            "enable-kube-dns": self.enable_kube_dns,
        }

    def dns_ready(self):
        """
        Return True if we have all DNS info from the master.
        """
        keys = ["port", "domain", "sdn-ip", "enable-kube-dns"]
        dns_info = self.get_dns()
        return set(dns_info.keys()) == set(keys) and dns_info["enable-kube-dns"] is not None
