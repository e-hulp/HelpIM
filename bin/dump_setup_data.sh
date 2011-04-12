#!/bin/sh

# Command to use when dumping the the setup for use on an other installation
# Usage:
#
# ./bin/dump_setup_data.sh > helpim/fixtures/setup_data.json

`dirname $0`/manage.py dumpdata flatpages auth.group conversations --indent 4 --natural
