// @ts-nocheck
/**
 * REFERENCE SNIPPET — copy the relevant parts into your Backstage backend:
 *   packages/backend/src/index.ts
 *
 * This registers all kubesmith scaffolder actions so templates can use:
 *   kubesmith:create-cluster
 *   kubesmith:deploy
 *   kubesmith:create-monitor
 */

import { createBackend } from '@backstage/backend-defaults';
import {
  createClusterAction,
  createDeployAppAction,
  createMonitorAction,
} from '@kubesmith/backstage-plugin-scaffolder-backend-module-kubesmith';

const backend = createBackend();

// ... your existing backend.add() calls ...

// Register kubesmith scaffolder actions
backend.add(
  import('@backstage/plugin-scaffolder-backend').then(({ scaffolderPlugin }) =>
    scaffolderPlugin({
      actions: (config) => [
        createClusterAction(config),
        createDeployAppAction(config),
        createMonitorAction(config),
      ],
    }),
  ),
);

backend.start();
