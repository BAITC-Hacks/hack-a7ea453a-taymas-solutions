import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
var projectDir = path.dirname(fileURLToPath(import.meta.url));
var outputDir = path.resolve(projectDir, '../out');
// The Python pipeline writes ../out. This small adapter exposes it during local
// development and embeds available CSVs under dist/out for a static demo build.
export default defineConfig({
    plugins: [react(), {
            name: 'money-graph-output-files',
            configureServer: function (server) {
                server.middlewares.use('/out', function (request, response, next) {
                    var _a;
                    var name = (_a = request.url) === null || _a === void 0 ? void 0 : _a.replace(/^\//, '');
                    if (!name || name.includes('..'))
                        return next();
                    var file = path.join(outputDir, name);
                    if (!fs.existsSync(file))
                        return next();
                    response.setHeader('Content-Type', 'text/csv; charset=utf-8');
                    response.end(fs.readFileSync(file));
                });
            },
            generateBundle: function () {
                if (!fs.existsSync(outputDir))
                    return;
                for (var _i = 0, _a = ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv', 'edge_table.csv']; _i < _a.length; _i++) {
                    var name_1 = _a[_i];
                    var file = path.join(outputDir, name_1);
                    if (fs.existsSync(file))
                        this.emitFile({ type: 'asset', fileName: "out/".concat(name_1), source: fs.readFileSync(file) });
                }
            },
        }],
    server: {
        fs: { allow: [path.resolve(projectDir, '..')] },
    },
});
