# MoePus VPM Repository

Personal VRChat Creator Companion (VCC) & ALCOM package listing for packages maintained by MoePus.

- **Listing Page**: https://moepus.github.io/vpm/
- **VCC Repository URL**: `https://moepus.github.io/vpm/index.json`

## Adding a New Package Repository

Append `"MoePus/<RepoName>"` to `githubRepos` in [`source.json`](source.json) and push to `main`. GitHub Actions will automatically collect published GitHub Releases (`*.zip` + `package.json`) and redeploy `index.json` and `index.html`.
