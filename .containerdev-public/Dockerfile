FROM registry.fedoraproject.org/fedora:latest
MAINTAINER Rick Elrod <relrod@redhat.com>

RUN dnf -y install \
    vim git python3-pip && \
    pip3 install python-fedora python-freeipa requests

VOLUME /project

WORKDIR /project
