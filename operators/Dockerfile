# Shared image for every deploy-and-operate operator. One build; the OPERATOR env selects which
# operator this container serves over the Mission Runtime /invoke contract (operators/service.py):
#   OPERATOR=infra | edge-sentinel | operate | agentic-compliance | agentic-privacy
#
# The infra operator shells out to terraform/ansible/aws/kubectl/helm, so the full toolchain ships
# here — that way an operator runs anywhere Docker runs, with no host toolchain. SIM=1 short-circuits
# every operator to its in-process simulator (no cloud calls) for a $0 dry run.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl unzip git ca-certificates openssh-client && rm -rf /var/lib/apt/lists/*

# terraform + kubectl + helm — pinned; installed for the infra operator (harmless for the others)
ARG TF_VERSION=1.9.8
ARG KUBECTL_VERSION=v1.30.4
RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip" \
      -o /tmp/tf.zip && unzip /tmp/tf.zip -d /usr/local/bin && rm /tmp/tf.zip
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
      -o /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl
RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

RUN pip install --no-cache-dir \
      fastapi uvicorn "pydantic>=2.6" pyyaml \
      boto3 awscli ansible-core kubernetes

WORKDIR /app
# repo code (operators/, infra/, aws_demo/) is mounted at /app; the agentic-os kernel at
# /opt/agentic-os (see deploy/compose.demo.yml volumes). Copying is unnecessary for the demo and
# keeps the image reusable across code edits without a rebuild.
ENV AGENTIC_OS_ROOT=/opt/agentic-os \
    PYTHONPATH=/app:/opt/agentic-os:/opt/agentic-os/apps \
    OPERATOR=infra \
    PORT=8230 \
    SIM=1

EXPOSE 8230 8241 8242 8243 8244
# operators/service.py builds the operator named by $OPERATOR and mounts /capabilities + /invoke
CMD ["sh", "-c", "uvicorn operators.service:app --host 0.0.0.0 --port ${PORT}"]
