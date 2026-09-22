# Security

Report vulnerabilities privately through the repository's GitHub **Report a vulnerability** / security advisory page. Include a minimal reproduction, affected commit or version, impact, and a proposed mitigation if known. Do not include credentials, cookies, personal browser data, or production traces in a public issue.

OpenUltra is an experimental local automation runtime. Use the dedicated Chrome profile, restrict allowed domains, provide deterministic completion checks, and review every workflow before allowing destructive or financial actions. Browser pages and voice-recognition services may communicate over the network even though Laya inference runs locally. Supported security fixes target the current `main` branch; older commits may not receive patches.
