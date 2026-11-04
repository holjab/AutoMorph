# ---- Stage 1: build a trimmed Java 21 runtime & fetch morphe ----
FROM alpine:3.21 AS jre-build

RUN apk add --no-cache \
    openjdk21-jdk \
    ca-certificates-bundle \
    binutils

ARG JLINK_MODULES=java.base,java.desktop,java.logging,java.naming,java.sql,java.xml,jdk.net,java.management,jdk.unsupported,jdk.crypto.ec

RUN mkdir -p /rootfs/opt/java /rootfs/automorph/tools && \
    jlink \
    --add-modules ${JLINK_MODULES} \
    --strip-debug \
    --no-man-pages \
    --no-header-files \
    --compress=zip-9 \
    --output /rootfs/opt/java/runtime

RUN KEYSTORE=$(find /usr/lib/jvm -name cacerts | head -n 1) && \
    test -n "$KEYSTORE" && \
    cp -L "$KEYSTORE" /rootfs/opt/java/runtime/lib/security/cacerts && \
    test -s /rootfs/opt/java/runtime/lib/security/cacerts && \
    /rootfs/opt/java/runtime/bin/java -version

ARG MORPHE_VERSION=2.0.0
RUN wget -q -O /rootfs/automorph/tools/morphe-desktop-${MORPHE_VERSION}-all.jar \
    "https://github.com/MorpheApp/morphe-desktop/releases/download/v${MORPHE_VERSION}/morphe-desktop-${MORPHE_VERSION}-all.jar" && \
    ln -sf "/automorph/tools/morphe-desktop-${MORPHE_VERSION}-all.jar" /rootfs/automorph/tools/morphe.jar


# ---- Stage 2: app image ----
FROM python:3.12-alpine3.21

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JAVA_HOME=/opt/java/runtime
ENV PATH="${JAVA_HOME}/bin:${PATH}"

COPY --from=jre-build /rootfs/ /

WORKDIR /automorph

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8080"]