import { createTemplateAction } from '@backstage/plugin-scaffolder-node';
import { Config } from '@backstage/config';

interface KubesmithCreateClusterInput {
  environmentId: string;
  clusterName: string;
  nodeCount: number;
}

async function getToken(baseUrl: string, username: string, password: string): Promise<string> {
  const res = await fetch(`${baseUrl}/api/v1/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`kubesmith auth failed (${res.status})`);
  const data = await res.json() as { token: string };
  return data.token;
}

export function createClusterAction(config: Config) {
  const baseUrl = config.getString('kubesmith.baseUrl');
  const username = config.getString('kubesmith.username');
  const password = config.getString('kubesmith.password');

  return createTemplateAction<KubesmithCreateClusterInput>({
    id: 'kubesmith:create-cluster',
    description: 'Provision a new Kubernetes cluster via kubesmith',
    schema: {
      input: {
        required: ['environmentId', 'clusterName', 'nodeCount'],
        type: 'object',
        properties: {
          environmentId: { type: 'string' },
          clusterName:   { type: 'string' },
          nodeCount:     { type: 'integer', minimum: 1, maximum: 20 },
        },
      },
      output: {
        type: 'object',
        properties: {
          clusterId:    { type: 'string' },
          jobId:        { type: 'string' },
          dashboardUrl: { type: 'string' },
        },
      },
    },

    async handler(ctx) {
      const { environmentId, clusterName, nodeCount } = ctx.input;

      ctx.logger.info(`Provisioning cluster "${clusterName}" (${nodeCount} nodes) in environment ${environmentId}`);

      const token = await getToken(baseUrl, username, password);

      const res = await fetch(`${baseUrl}/api/v1/clusters`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          name: clusterName,
          node_count: nodeCount,
          environment_id: environmentId,
        }),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`kubesmith create-cluster failed (${res.status}): ${text}`);
      }

      const data = await res.json() as { cluster_id: string; job_id: string };

      ctx.logger.info(`Cluster ${data.cluster_id} queued — job ${data.job_id}`);

      ctx.output('clusterId',    data.cluster_id);
      ctx.output('jobId',        data.job_id);
      ctx.output('dashboardUrl', `${baseUrl}/#clusters`);
    },
  });
}
