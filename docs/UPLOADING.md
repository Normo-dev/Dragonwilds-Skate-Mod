# Pushing the source repository

This `Dragonwilds-Skate-Mod` folder is a standalone Git repository on `main`.
Its `origin` is [Normo-dev/Dragonwilds-Skate-Mod](https://github.com/Normo-dev/Dragonwilds-Skate-Mod),
and the existing GitHub initial commit is preserved as the local branch's parent.
Open this folder itself in IntelliJ IDEA.

Review the local commits and upload them yourself:

```powershell
git status --short
git log --oneline -3
git remote -v
git push -u origin main
```

The source folder ignores local builds, private game files, caches, saves and
agent instruction files. No game content is included in Git. If Git reports
that the remote moved after this checkout was prepared, review and integrate
those changes before pushing; do not force-push over them.
