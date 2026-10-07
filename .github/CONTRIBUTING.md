# Contributing

Thank you for helping improve neurwerk.base - Dify Add-on.

## Issues

Use [GitHub Issues](https://github.com/neurwerk/k8s_stack_addon_dify/issues) for reproducible bugs and clearly scoped feature requests. Check for an existing issue first and keep each report short and focused. Include relevant versions and reproduction steps when available. Never include secrets or private data.

Report suspected vulnerabilities privately by following [SECURITY.md](../SECURITY.md).

## Pull requests

Keep changes focused, use the repository's pull request template, and link any related issue. Update affected documentation and tests, run `make check`, and include the results in the pull request. Never commit credentials, private data, or generated caches. Image builds and publication are operator-only: agents must not run `deploy.sh`, build Dify images, or publish them.
