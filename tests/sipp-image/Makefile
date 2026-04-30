# SIPp with SSL+PCAP container image.
APP_NAME   = sipp
ORG        = nethesis
TAG       ?= dev
IMAGE      = ghcr.io/$(ORG)/$(APP_NAME):$(TAG)

.PHONY: build push all
build:
	buildah bud -t $(IMAGE) -f Containerfile .
push:
	podman push $(IMAGE)
all: build push
