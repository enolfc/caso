# -*- coding: utf-8 -*-

# Copyright 2014 Spanish National Research Council (CSIC)
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import ceilometerclient.client
from oslo.config import cfg
import six

from caso.extract import nova
from caso import log

CONF = cfg.CONF
CONF.import_opt("user", "caso.extract.base", "extractor")
CONF.import_opt("password", "caso.extract.base", "extractor")
CONF.import_opt("endpoint", "caso.extract.base", "extractor")
CONF.import_opt("insecure", "caso.extract.base", "extractor")

LOG = log.getLogger(__name__)


class CeilometerExtractor(nova.OpenStackExtractor):
    def _get_ceilometer_client(self, tenant):
        return ceilometerclient.client.get_client(
            '2',
            os_auth_url=CONF.extractor.endpoint,
            os_username=CONF.extractor.user,
            os_password=CONF.extractor.password,
            os_tenant_name=tenant,
            insecure=CONF.extractor.insecure)

    def _build_query(self, project_id=None, queries={},
                     start_date=None, end_date=None):
        q = []
        if project_id:
            q.append({'field': 'project_id', 'value': project_id})
        for k, v in six.iteritems(queries):
            q.append({'field': k, 'value': v})
        if start_date:
            q.append({'field': 'timestamp', 'op': 'ge', 'value': start_date})
        if end_date:
            q.append({'field': 'timestamp', 'op': 'le', 'value': end_date})
        return q

    def _fill_metric(self, metric_name, samples, record,
                     unit_conv=lambda v: v):
        """Fills a given metric in the record.

        Assumes samples are for a cumulative metric that may reset
        for given events (e.g. suspending an instance)

        conv is a function that converts the metric to the units
             requested by the usage record
        """

        last_value = 0
        accum = 0
        values = []
        for sample in samples:
            # assumes that samples are sorted from newer to older
            sample_value = unit_conv(sample.counter_volume)
            values.append(sample_value)
            if last_value < sample_value:
                accum += sample_value
            last_value = sample_value
        record.__dict__[metric_name] = accum

    def _fill_cpu_metric(self, cpu_samples, record):
        self._fill_metric('cpu_duration', cpu_samples, record,
                          # convert ns to s
                          unit_conv=lambda v: int(v / 1e9))

    def _fill_net_metric(self, metric_name, net_samples, record):
        self._fill_metric(metric_name, net_samples, record,
                          # convert bytes to GB
                          unit_conv=lambda v: int(v / 2 ** 30))

    def extract_for_tenant(self, tenant, lastrun):
        records = super(CeilometerExtractor,
                        self).extract_for_tenant(tenant, lastrun)
        # Try and except here
        ks_conn = self._get_keystone_client(tenant)
        conn = self._get_ceilometer_client(tenant)
        # See comment in nova.py, remove TZ from the dates.
        lastrun = lastrun.replace(tzinfo=None)

        # for each of the records built by nova, get the samples
        # from ceilometer
        for r_id, r in six.iteritems(records):
            cpu_query = self._build_query(ks_conn.tenant_id,
                                          {'resource_id': r_id})
            cpu = conn.samples.list(meter_name='cpu', q=cpu_query)
            self._fill_cpu_metric(cpu, r)

            net_query = self._build_query(ks_conn.tenant_id,
                                          {'metadata.instance_id': r_id})
            net_in = conn.samples.list(meter_name='network.incoming.bytes',
                                       q=net_query)
            self._fill_net_metric('network_in', net_in, r)
            net_out = conn.samples.list(meter_name='network.outcoming.bytes',
                                        q=net_query)
            self._fill_net_metric('network_out', net_out, r)

        return records
