# Security

## Reporting a vulnerability

Report vulnerabilities **privately** to the repository owner's team: use GitHub's "Report a vulnerability"
button under the Security tab, or contact the maintainers directly. **Do not open a public issue**, and do not
post details in pull requests or discussions until a fix is out.

Please include what is affected (file, action or feature), the commit you tested, how to reproduce it, and what
an attacker could do. We aim to acknowledge reports within a few working days and will tell you when a fix
ships.

## Scope

OmegaLibrary runs inside an OmegaHive host. Problems in the host itself (sign-in, grants, the sandbox, the
program contract) belong to the omegahive repository; report them the same way. If you are not sure which
side a problem is on, report it here.

## Secrets

Never commit secrets: `.env*` files, API keys, tokens, or session and sign-in files. This app reads no
secrets of its own; the host supplies identity and grants.
