# Pi Integration

This checkout uses Pi as the preferred harness for MathModelAgent Skills.

- Keep upstream skills in `skills/` unchanged when possible.
- Put Pi-specific compatibility code in `pi/` and `scripts/start_pi.ps1`.
- Do not invoke Claude Code or depend on `.claude/settings.json` for Pi runs.
- Generate contest artifacts in a separate workspace, never in this repository.
- Use `PI.md` for setup and execution instructions.
- Validate PowerShell changes with the PowerShell parser before committing.
- Preserve the upstream personal-use-only license restrictions in `docs/md/License.md`.
