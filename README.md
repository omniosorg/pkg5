This is the OmniOS child of pkg(7)

Branches:

master

	This is the default development branch for OmniOS. Besides ongoing
	development, this branch may be refreshed with merges from upstream
	branches. Commits in this branch are able to be cherry-picked by any
	branch that wants them. Just follow the CDDL and give credit where due.

r151XXX

	As we issue releases, we branch for each release.

## Testing

In order to run the test-suite with 8 jobs in parallel:

```terminal
$ dmake -C src test JOBS=8
```

