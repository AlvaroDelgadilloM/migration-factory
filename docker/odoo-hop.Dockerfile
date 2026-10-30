# One OpenUpgrade hop: the official Odoo image of the target version + OCA/OpenUpgrade at a pinned commit + the task runner.
#   docker build -f docker/odoo-hop.Dockerfile --build-arg ODOO=14.0 --build-arg OU_COMMIT=<sha> .
# openupgradelib and its pure-Python dependencies are pinned (14.0+: the package of OUL_COMMIT over the PyPI release, whose
# metadata it keeps; master is not on PyPI and its setup.py needs git); lxml/psycopg2 come from the Odoo image (nothing is compiled).
ARG ODOO=19.0
FROM odoo:${ODOO}
ARG ODOO
ARG OU_COMMIT
ARG OUL_COMMIT=5dfe3b485da5cfaa1f8c528c183cbdd0bc8248b0
USER root
RUN (pip3 install --no-cache-dir --no-deps --break-system-packages openupgradelib==3.13.7 cssselect==1.2.0 2>/dev/null \
     || pip3 install --no-cache-dir --no-deps openupgradelib==3.13.7 cssselect==1.2.0 importlib_metadata==6.7.0 zipp==3.15.0 typing_extensions==4.7.1) \
    && if [ "${ODOO}" != "13.0" ]; then \
         d=$(python3 -c "import openupgradelib, os; print(os.path.dirname(openupgradelib.__file__))") \
         && curl -fsSL --retry 4 "https://codeload.github.com/OCA/openupgradelib/tar.gz/${OUL_COMMIT}" | tar -xz -C "$d" --strip-components=2 "openupgradelib-${OUL_COMMIT}/openupgradelib"; fi \
    && python3 -c "import openupgradelib" \
    && mkdir -p /opt/openupgrade /opt/mf /hop-logs \
    && curl -fsSL --retry 4 "https://codeload.github.com/OCA/OpenUpgrade/tar.gz/${OU_COMMIT}" | tar -xz -C /opt/openupgrade --strip-components=1 \
    && chown odoo /hop-logs \
    && if [ "${ODOO}" = "13.0" ]; then rm -rf /usr/lib/python3/dist-packages/odoo /usr/bin/odoo; fi
# 13.0 is a full fork of Odoo (its addons carry the migration scripts): the image's own Odoo is removed so no module can be
# loaded from it instead (it shadowed the fork's addons and their scripts never ran).
COPY docker/odoo-hop/runner.py docker/odoo-hop/preview.py /opt/mf/
ENV MF_ODOO_VERSION=${ODOO}
USER odoo
ENTRYPOINT ["python3", "/opt/mf/runner.py"]
