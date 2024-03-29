/*
 * This file and its contents are supplied under the terms of the
 * Common Development and Distribution License ("CDDL"), version 1.0.
 * You may only use this file in accordance with the terms of version
 * 1.0 of the CDDL.
 *
 * A full copy of the text of the CDDL should have accompanied this
 * source. A copy of the CDDL is also available via the Internet at
 * http://www.illumos.org/license/CDDL.
 */

/*
 * Copyright 2023 OmniOS Community Edition (OmniOSce) Association.
 */

#include <assert.h>
#include <err.h>
#include <errno.h>
#include <fcntl.h>
#include <libzfs.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <zone.h>
#include <sys/fcntl.h>

#define	QEMU_MAXARGS	0x80

static libzfs_handle_t *g_zfs;

static int
mount_dataset(zfs_handle_t *zhp, void *arg __unused)
{
	const char *ds = zfs_get_name(zhp);
	char *mp;

	if (zfs_mount(zhp, NULL, 0) == 0 && zfs_is_mounted(zhp, &mp))
		printf("Dataset: %s - mounted on %s\n", ds, mp);
	else
		printf("Dataset: %s\n", ds);

	return (0);
}

static int
get_one_dataset(zfs_handle_t *zhp, void *data)
{
	get_all_cb_t *cb = data;
	zfs_type_t type = zfs_get_type(zhp);

	if (zfs_iter_filesystems(zhp, get_one_dataset, data) != 0) {
		zfs_close(zhp);
		return (1);
	}

	if ((type & ZFS_TYPE_FILESYSTEM) == 0) {
		zfs_close(zhp);
		return(0);
	}

	libzfs_add_handle(cb, zhp);
	return (0);
}

static void
get_all_datasets(get_all_cb_t *cbp)
{
	(void) zfs_iter_root(g_zfs, get_one_dataset, cbp);
}

static void
mount_datasets(void)
{
	get_all_cb_t cb = { 0 };

	if ((g_zfs = libzfs_init()) == NULL) {
		fprintf(stderr, "Failed to initialise ZFS library\n");
		return;
	}

	get_all_datasets(&cb);

	zfs_foreach_mountpoint(g_zfs, cb.cb_handles, cb.cb_used,
	    mount_dataset, NULL, B_FALSE);

	for (uint_t i = 0; i < cb.cb_used; i++)
		zfs_close(cb.cb_handles[i]);
	free(cb.cb_handles);

	libzfs_fini(g_zfs);
}

static void
setup_descriptors(void)
{
	int fd;

	fd = open("/dev/null", O_WRONLY);
	assert(fd >= 0);
	if (fd != STDIN_FILENO)
		(void) dup2(fd, STDIN_FILENO);
	(void) close(fd);

	fd = open("/tmp/init.log", O_WRONLY|O_CREAT|O_TRUNC, 0644);
	assert(fd >= 0);
	(void) dup2(fd, STDOUT_FILENO);
	(void) dup2(fd, STDERR_FILENO);
	(void) close(fd);
	(void) setvbuf(stdout, NULL, _IONBF, 0);
	(void) setvbuf(stderr, NULL, _IONBF, 0);
}

static void
setup_environment(void)
{
	FILE *fp;
	char *line = NULL, *cp;
	size_t linecap = 0;

	if ((fp = fopen("/etc/qemu.env", "r")) == NULL)
		return;

	while (getline(&line, &linecap, fp) > 0) {
		if (*line == '#' || *line == '\n')
			continue;
		if ((cp = strchr(line, '\n')) != NULL)
			*cp = '\0';
		if ((cp = strchr(line, '=')) == NULL) {
			fprintf(stderr, "Bad env line %s\n", line);
			continue;
		}
		*cp++ = '\0';
		if (setenv(line, cp, 1) == 0)
			printf("setenv %s=%s\n", line, cp);
		else
			fprintf(stderr, "Failed to setenv %s=%s\n", line, cp);
	}

	free(line);
	fclose(fp);
}

static void
setup_arguments(char **argv, size_t *argc)
{
	FILE *fp;
	char *line = NULL, *cp;
	size_t linecap = 0;

	if ((fp = fopen("/etc/qemu.cfg", "r")) == NULL)
		return;

	while (getline(&line, &linecap, fp) > 0) {
		if (*line == '#' || *line == '\n')
			continue;
		if ((cp = strchr(line, '\n')) != NULL)
			*cp = '\0';

		if (*argc >= QEMU_MAXARGS)
			errx(EXIT_FAILURE, "Too many arguments");

		argv[*argc] = strdup(line);
		if (argv[*argc] == NULL) {
			err(EXIT_FAILURE,
			    "Could not allocate memory for argument");
		}
		(*argc)++;
	}

	free(line);
	fclose(fp);
}

int
main(int argc __unused, char **argv __unused)
{
	char zonename[ZONENAME_MAX];

	char *qargv[QEMU_MAXARGS] = { 0 };
	size_t qargc = 1;

	if (getzonenamebyid(getzoneid(), zonename, sizeof (zonename)) < 0)
		err(EXIT_FAILURE, "Could not determine zone name");

	setup_arguments(qargv, &qargc);
	setup_descriptors();
	mount_datasets();
	setup_environment();

	(void) sleep(5);

	if (asprintf(&qargv[0], "qemu-system-%s", zonename) == -1)
		qargv[0] = "qemu";

	(void) execv("/qemu-system", qargv);
	err(EXIT_FAILURE, "execv failed");

	/* NOTREACHED */
	return (0);
}
