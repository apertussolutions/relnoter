FROM asciidoctor/docker-asciidoctor

LABEL MAINTAINERS="Daniel P. Smith <dpsmith@apertussolutions.com>"

RUN apk add --no-cache \
    git \
    jq \
    py2-pygit2 \
    py2-requests \
  && apk add --no-cache --virtual .makedepends \
    py2-pip \
  && pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir sh \
  && apk del -r --no-cache .makedepends

ADD generate_release.py /release/
ADD generate_pdf.sh /release/
ADD openxt-theme.yml /release/
ADD ubuntu-font-family/ /release/ubuntu-font-family/
