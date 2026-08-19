# Publishing this repository

The working tree is already a git repository with one commit. Two steps left.

## 1. Create the empty repository on GitHub

github.com → **New repository**

- **Name:** `netops-observability-toolkit`
- **Description:** Zabbix audit tooling, Grafana 12 dashboards and runbooks for ISP network operations. Finds the panels that silently show nothing.
- **Public**
- **Do not** initialise with README, .gitignore or license — this tree already has them

## 2. Push

```bash
cd netops-observability-toolkit

git remote add origin https://github.com/<your-user>/netops-observability-toolkit.git
git branch -M main
git push -u origin main
```

If it asks for a password, use a personal access token (Settings → Developer
settings → Personal access tokens), not your account password.

## 3. Worth doing right after

- Add topics: `zabbix`, `grafana`, `netflow`, `isp`, `network-monitoring`,
  `observability`, `mikrotik`, `huawei`, `gpon`
- Set the About description and pin the repository on your profile
- Check the rendered README on the repository home page
