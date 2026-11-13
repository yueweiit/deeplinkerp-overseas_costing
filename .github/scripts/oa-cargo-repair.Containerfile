ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG APP_REVISION
USER frappe
COPY --chown=frappe:frappe . /home/frappe/frappe-bench/apps/overseas_costing
RUN test "${#APP_REVISION}" = 40 \
 && APP_REVISION="$APP_REVISION" /home/frappe/frappe-bench/env/bin/python -c \
 'import json,os,pathlib; p=pathlib.Path("/home/frappe/frappe-bench/app-revisions.json"); data=json.loads(p.read_text()); data["overseas_costing"]=os.environ["APP_REVISION"]; p.write_text(json.dumps(data,indent=2)+"\n")'
RUN cp -a assets/. sites/assets/ \
 && /home/frappe/frappe-bench/env/bin/python apps/overseas_costing/overseas_costing/scripts/build_workbench_assets.py \
 && /home/frappe/frappe-bench/env/bin/python apps/overseas_costing/overseas_costing/scripts/build_air_sea_assets.py \
 && bench build --app overseas_costing --production \
 && cp -a sites/assets/. assets/
