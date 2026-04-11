import { createTemplateAction } from '@backstage/plugin-scaffolder-node';
import { Config } from '@backstage/config';

interface KubesmithDeployInput {
  clusterId: string;
  appName: string;
  namespace: string;
  deployType: 'helm' | 'manifest';
  // helm
  chartRepo?: string;
  chartName?: string;
  chartVersion?: string;
  valuesOverride?: string;
  // manifest
  manifest?: string;
}

async function getToken(baseUrl: string, username: string, password: string): Promise<string> {
  const res = await fetch(`${baseUrl}/api/v1/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`kubesmith auth failed (${res.status}): ${text}`);
  }
  const data = await res.json() as { token: string };
  return data.token;
}

export function createDeployAppAction(config: Config) {
  const baseUrl = config.getString('kubesmith.baseUrl');
  const username = config.getString('kubesmith.username');
  const password = config.getString('kubesmith.password');

  return createTemplateAction<KubesmithDeployInput>({
    id: 'kubesmith:deploy',
    description: 'Deploy a Helm chart or manifest to a kubesmith cluster via AppDeployment CR',
    schema: {
      input: {
        required: ['clusterId', 'appName', 'namespace', 'deployType'],
        type: 'object',
        properties: {
          clusterId:      { type: 'string' },
          appName:        { type: 'string' },
          namespace:      { type: 'string' },
          deployType:     { type: 'string', enum: ['helm', 'manifest'] },
          chartRepo:      { type: 'string' },
          chartName:      { type: 'string' },
          chartVersion:   { type: 'string' },
          valuesOverride: { type: 'string' },
          manifest:       { type: 'string' },
        },
      },
      output: {
        type: 'object',
        properties: {
          dashboardUrl: { type: 'string' },
          detailUrl:    { type: 'string' },
        },
      },
    },

    async handler(ctx) {
      const {
        clusterId, appName, namespace, deployType,
        chartRepo, chartName, chartVersion, valuesOverride, manifest,
      } = ctx.input;

      ctx.logger.info(`Deploying ${appName} (${deployType}) to cluster ${clusterId}/${namespace}`);

      const token = await getToken(baseUrl, username, password);

      const body: Record<string, unknown> = {
        name: appName,
        namespace,
        deploy_type: deployType,
      };

      if (deployType === 'helm') {
        body.chart_repo    = chartRepo;
        body.chart_name    = chartName;
        body.chart_version = chartVersion ?? null;
        body.values_override = valuesOverride ?? null;
      } else {
        body.manifest = manifest;
      }

      const res = await fetch(
        `${baseUrl}/api/v1/clusters/${clusterId}/deployments`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
        },
      );

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`kubesmith deploy failed (${res.status}): ${text}`);
      }

      ctx.logger.info(`AppDeployment ${appName} created successfully`);

      ctx.output('dashboardUrl', `${baseUrl}/#deployments`);
      ctx.output('detailUrl',    `${baseUrl}/api/v1/clusters/${clusterId}/deployments/${appName}?namespace=${namespace}`);
    },
  });
}
