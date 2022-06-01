# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of tls-certificates interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import dataclasses
import json
import logging
from functools import cached_property
from typing import List, Mapping, Optional

import jsonschema
from ops.charm import RelationBrokenEvent
from ops.framework import Object

log = logging.getLogger(__name__)


@dataclasses.dataclass
class Certificate:
    """Represent a Certificate."""

    cert_type: str
    common_name: str
    cert: str
    key: str


class CertificatesRequires(Object):
    """Requires side of certificates relation."""

    LIMIT = 1
    SCHEMA = {
        "type": "object",
        "properties": {
            "ca": dict(type="string"),
            "client.cert": dict(type="string"),
            "client.key": dict(type="string"),
        },
        "required": [
            "ca",
            "client.cert",
            "client.key",
        ],
    }
    IGNORE_FIELDS = {
        "egress-subnets",
        "ingress-address",
        "private-address",
    }

    def __init__(self, charm, endpoint="certificates"):
        super().__init__(charm, f"relation-{endpoint}")
        self.charm = charm
        self.endpoint = endpoint
        self._unit_name = self.charm.unit.name.replace("/", "_")
        schema_properties = self.SCHEMA["properties"]
        schema_properties[f"{self._unit_name}.processed_client_requests"] = dict(
            type="object",
            additionalProperties=dict(
                type="object",
                properties=dict(
                    cert=dict(type="string"),
                    key=dict(type="string"),
                ),
            ),
        )
        schema_properties[f"{self._unit_name}.server.cert"] = dict(type="string")
        schema_properties[f"{self._unit_name}.server.key"] = dict(type="string")
        self.charm.framework.observe(self.charm.on.certificates_relation_joined, self._joined)

    def _joined(self, event=None):
        event.relation.data[self.charm.unit]["unit-name"] = self._unit_name

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
                return f"Missing required {self.endpoint}"
            return f"Waiting for {self.endpoint}"

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

    @property
    def ca(self):
        """The ca value."""
        return self._value("ca")

    @property
    def client_certs(self) -> List[Certificate]:
        """Certificate instances for all available client certs."""
        field = "{}.processed_client_requests".format(self._unit_name)
        certs_data = self._value(field) or {}
        return [
            Certificate("client", common_name, cert["cert"], cert["key"])
            for common_name, cert in certs_data.items()
        ]

    @property
    def client_certs_map(self) -> Mapping[str, Certificate]:
        """Certificate instances by their `common_name`."""
        return {cert.common_name: cert for cert in self.client_certs}

    def request_client_cert(self, cn, sans):
        """Request Client certificate for charm.

        Request a client certificate and key be generated for the given
        common name (`cn`) and list of alternative names (`sans`).
        This can be called multiple times to request more than one client
        certificate, although the common names must be unique.  If called
        again with the same common name, it will be ignored.
        """
        if not self.relation:
            return
        # assume we'll only be connected to one provider
        data = self.relation.data[self.charm.unit]
        requests = data.get("client_cert_requests", {})
        requests[cn] = {"sans": sans}
        data["client_cert_requests"] = json.dumps(requests)
