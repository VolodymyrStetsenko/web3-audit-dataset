# Security Policy

## Supported version

Security fixes are applied to the latest revision on the default branch.

## Reporting

Report vulnerabilities privately through GitHub Security Advisories. Do not open a public issue for credential exposure, unsafe archive handling, path traversal, command execution, or private-source disclosure.

Include the affected command, source type, impact, reproduction steps, and a minimal synthetic fixture. Do not attach real credentials or private corpus material.

## Operational model

All collected content is untrusted. The pipeline stores and indexes source files but does not execute them. Operators should isolate downstream agent systems, mount research corpora with restrictive options where practical, and never grant retrieved content authority over shell commands, wallets, signing keys, or deployment credentials.