from pathlib import Path
import os
import re
import subprocess
import textwrap

import pytest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "deploy-overseas-costing.yml"
)


def test_only_production_repository_deploys_and_rebuilds_cannot_overlap():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    assert "if: github.repository == 'yueweiit/DeepLinkERP'" in deploy
    assert "group: overseas-costing-production-deploy" in deploy
    assert "cancel-in-progress: false" in deploy
    assert "github.repository" not in workflow.split("\n  deploy:\n", maxsplit=1)[0]


def test_deploy_job_checks_out_repository_before_running_asset_script():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy_block = workflow.split("\n  deploy:\n", maxsplit=1)[1]

    checkout = "uses: actions/checkout@v4"
    asset_script = ".github/scripts/sync_and_verify_assets.sh"

    assert checkout in deploy_block
    assert deploy_block.index(checkout) < deploy_block.index(asset_script)


def test_overseas_costing_ci_no_longer_owns_inventory_page_assets() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    test_job = workflow.split("\n  deploy:\n", maxsplit=1)[0]

    assert "build_inventory_assets.py" not in test_job
    assert "categorized_inventory_detail.bundle.js" not in test_job
    assert "inventory_location_detail" not in test_job


def test_inventory_ownership_cutover_requires_the_paired_branding_commit() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    test_job, deploy = workflow.split("\n  deploy:\n", maxsplit=1)
    match = re.search(
        r"REQUIRED_INVENTORY_BRANDING_SHA:\s*([0-9a-f]{40})",
        workflow,
    )

    assert match, "deployment must pin the branding ownership commit"
    required_sha = match.group(1)
    assert 'git fetch --no-tags --depth 1 https://github.com/yueweiit/deeplinkerp-deeplinkerp_branding.git "$REQUIRED_INVENTORY_BRANDING_SHA"' in test_job
    assert 'test "$(git rev-parse FETCH_HEAD)" = "$REQUIRED_INVENTORY_BRANDING_SHA"' in test_job
    assert test_job.index("Verify paired inventory owner commit") < test_job.index(
        "Run tests affected by this update"
    )
    assert f"REQUIRED_INVENTORY_BRANDING_SHA='{required_sha}'" in deploy

    content_match = re.search(
        r"REQUIRED_INVENTORY_BRANDING_CONTENT_SHA256:\s*([0-9a-f]{64})",
        deploy,
    )
    assert content_match, "deployment must pin the inventory source content"
    assert (
        f"REQUIRED_INVENTORY_BRANDING_CONTENT_SHA256='{content_match.group(1)}'"
        in deploy
    )


def test_upgrade_blocks_migration_until_inventory_ownership_is_present() -> None:
    script = (
        WORKFLOW_PATH.parent.parent / "scripts" / "upgrade_and_migrate_erp.sh"
    ).read_text(encoding="utf-8")

    ownership_gate = "Verify paired inventory ownership before migrate"
    migrate = './migrate_site.sh "$site_name"'
    assert (
        'required_inventory_branding_content_sha256="${3:?required inventory branding content sha256 is required}"'
        in script
    )
    assert ownership_gate in script
    assert "hashlib.sha256" in script
    assert "inventory branding content digest mismatch" in script
    assert 'git -C "$branding_app"' not in script
    assert "deeplinkerp_branding/services/inventory_detail_service.py" in script
    assert "inventory_original_location_snapshot/inventory_original_location_snapshot.json" in script
    assert "overseas_costing/services/inventory_location_service.py" in script
    assert script.index(ownership_gate) < script.index(migrate)


def test_release_verifier_checks_transferred_pages_doctype_service_and_assets() -> None:
    script = (
        WORKFLOW_PATH.parent.parent / "scripts" / "sync_and_verify_assets.sh"
    ).read_text(encoding="utf-8")

    for marker in (
        "deeplinkerp_branding.services.inventory_detail_service",
        "inventory-location-detail",
        "semi-finished-inventory-detail",
        "finished-goods-inventory-detail",
        "mold-inventory-detail",
        "Inventory Original Location Snapshot",
        "Deeplinkerp Branding",
        "/assets/deeplinkerp_branding/js/inventory_detail.bundle.js",
        "/assets/deeplinkerp_branding/css/inventory_detail.bundle.css",
    ):
        assert marker in script
    assert "get_inventory_movement_context" in script
    assert "prepare_inventory_stock_entry" in script


def test_deploy_prewarms_packing_cache_before_switching_frontend_assets() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]

    prewarm = "overseas_costing.services.packing_sheet_cache_service.prewarm_catalog_cache"
    assets = "Synchronize and verify frontend assets"
    assert prewarm in deploy
    assert deploy.index("Upgrade and migrate ERP") < deploy.index(prewarm) < deploy.index(assets)


def test_deploy_rechecks_company_rename_integrity_after_migrate() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    verification = (
        "overseas_costing.services.company_rename_service."
        "verify_company_renames_complete"
    )

    assert verification in deploy
    assert deploy.index("Upgrade and migrate ERP") < deploy.index(verification)


def test_deploy_transfers_and_verifies_company_inventory_inside_maintenance_window() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    execute = (
        "overseas_costing.services.company_inventory_transfer_service."
        "execute_company_inventory_transfer"
    )
    verify = (
        "overseas_costing.services.company_inventory_transfer_service."
        "verify_company_inventory_transfer_complete"
    )

    assert execute in deploy
    assert verify in deploy
    assert deploy.index("Verify company rename integrity") < deploy.index(execute)
    assert deploy.index(execute) < deploy.index(verify) < deploy.index(
        "Finalize production release"
    )
    transfer_step = deploy.split("- name: Transfer legacy Mexico inventory to YUEWEI MX", 1)[1].split("\n      - name:", 1)[0]
    assert "ServerAliveInterval=30" in transfer_step
    assert "ServerAliveCountMax=20" in transfer_step
    assert deploy.index("Check online maintenance response") < deploy.index(execute)


def test_cancelled_deploy_also_runs_recovery_before_temp_file_cleanup() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    rollback = deploy.split("- name: Rollback failed ERP release", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert "if: failure() || cancelled()" in rollback
    assert "rollback-safe" in rollback
    assert deploy.index("Rollback failed ERP release") < deploy.index(
        "Remove temporary deployment files"
    )


def test_deploy_cleans_legacy_route_revision_before_migrate() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    upgrade_script = (
        WORKFLOW_PATH.parent.parent / "scripts" / "upgrade_and_migrate_erp.sh"
    ).read_text(encoding="utf-8")

    cleanup = 'bench --site "$site_name" execute overseas_costing.install.before_migrate'
    assert ".github/scripts/upgrade_and_migrate_erp.sh" in deploy
    assert cleanup in upgrade_script
    assert upgrade_script.index("upgrade_bench.sh") < upgrade_script.index(cleanup) < upgrade_script.index("migrate_site.sh")
    assert "Inspect and normalize route revision before upgrade" in deploy
    assert ".github/scripts/prepare_route_revision_migration.sh" in deploy
    assert deploy.index("Prepare production rollback point") < deploy.index(
        "Inspect and normalize route revision before upgrade"
    ) < deploy.index("Upgrade and migrate ERP")


def test_route_revision_migration_script_backups_and_bounds_legacy_values() -> None:
    script = (
        WORKFLOW_PATH.parent.parent
        / "scripts"
        / "prepare_route_revision_migration.sh"
    ).read_text(encoding="utf-8")

    assert "backup --with-files" in script
    assert "information_schema.columns" in script
    assert "ALTER COLUMN `route_revision` SET DEFAULT 0" in script
    assert "NOT REGEXP ''^[0-9]+$''" in script
    assert "`route_revision` IS NULL" in script
    assert "SQL_SAFE_UPDATES = 0" in script
    assert "2147483647" in script


def test_production_deploy_requires_deepseek_and_installs_document_runtime() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    assert "DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}" in deploy
    assert 'test -n "$DEEPSEEK_API_KEY"' in deploy
    assert ".github/scripts/install_document_runtime.sh" in deploy
    assert ".github/scripts/configure_material_ai.sh" in deploy
    assert "verify_material_ai_runtime" in deploy
    assert "Preflight DeepSeek connection" in deploy
    assert deploy.index("Preflight DeepSeek connection") < deploy.index("Upgrade and migrate ERP")
    assert "Rollback failed ERP release" in deploy
    assert "if: failure()" in deploy
    assert "--kwargs '\"'\"'{\"check_connection\": True}'\"'\"'" in deploy
    assert "--kwargs '\"'\"'{\"check_connection\": true}'\"'\"'" not in deploy


def test_runtime_installer_backs_up_image_and_requires_ocr_tools() -> None:
    scripts = WORKFLOW_PATH.parent.parent / "scripts"
    installer = (scripts / "install_document_runtime.sh").read_text(encoding="utf-8")
    dockerfile = (scripts / "material-ai-runtime.Containerfile").read_text(encoding="utf-8")
    for package in ("poppler-utils", "tesseract-ocr", "tesseract-ocr-chi-sim", "antiword"):
        assert package in dockerfile
    assert "docker image tag" in installer
    assert "pre-material-ai" in installer
    assert "bench --site" in installer and "backup --with-files" in installer
    assert "tesseract --list-langs" in installer
    assert "chi_sim" in installer
    assert 'mode="${5:-install}"' in installer
    assert 'if [ "$mode" = "preflight" ]' in installer
    assert installer.count('restart frontend') >= 2
    assert 'script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in installer
    assert 'asset_sync_script="${ASSET_SYNC_SCRIPT:-}"' in installer
    assert 'asset_sync_script="$script_dir/sync_and_verify_assets.sh"' in installer
    assert 'COMPOSE_FILE="$compose_file" SITE_NAME="$site_name" bash "$asset_sync_script"' in installer


def test_material_ai_configuration_reloads_frontend_after_backend_restart() -> None:
    scripts = WORKFLOW_PATH.parent.parent / "scripts"
    configurator = (scripts / "configure_material_ai.sh").read_text(encoding="utf-8")

    assert configurator.index("restart backend") < configurator.index("restart frontend")
    assert 'script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in configurator
    assert 'asset_sync_script="${ASSET_SYNC_SCRIPT:-}"' in configurator
    assert 'asset_sync_script="$script_dir/sync_and_verify_assets.sh"' in configurator
    assert 'COMPOSE_FILE="$compose_file" SITE_NAME="$site_name" bash "$asset_sync_script"' in configurator


def test_failed_release_can_restore_image_database_files_and_site_config() -> None:
    scripts = WORKFLOW_PATH.parent.parent / "scripts"
    release = (scripts / "manage_material_ai_release.sh").read_text(encoding="utf-8")
    assert "pre-material-ai-release" in release
    assert "backup --with-files" in release
    assert 'restore "$remote_dir/' in release
    assert "--with-public-files" in release
    assert "--with-private-files" in release
    assert "site_config.json" in release
    assert "docker image tag \"$backup_image\" \"$base_image\"" in release
    assert "backend websocket queue-short queue-long scheduler frontend" in release


def test_database_rollback_supplies_the_container_root_password_non_interactively() -> None:
    release = (
        WORKFLOW_PATH.parent.parent / "scripts" / "manage_material_ai_release.sh"
    ).read_text(encoding="utf-8")
    rollback = release.split("rollback_release()", 1)[1].split(
        "rollback_safe_release()", 1
    )[0]

    assert "MYSQL_ROOT_PASSWORD" in rollback
    assert "--db-root-password" in rollback
    assert "printenv MYSQL_ROOT_PASSWORD" not in rollback


def test_database_migration_is_covered_by_a_full_maintenance_window() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    release = (WORKFLOW_PATH.parents[1] / "scripts" / "manage_material_ai_release.sh").read_text(
        encoding="utf-8"
    )

    prepare = release.split("prepare_release()", 1)[1].split("rollback_release()", 1)[0]
    assert prepare.index("set-maintenance-mode on") < prepare.index("backup --with-files")
    assert "ready-for-migration" in prepare
    assert "finish) finish_release" in release
    assert "restore_maintenance_mode" in release.split("rollback_release()", 1)[1]

    assert deploy.index("Prepare production rollback point") < deploy.index("Upgrade and migrate ERP")
    assert deploy.index("Check online maintenance response") < deploy.index("Finalize production release")
    assert deploy.index("Finalize production release") < deploy.index("Rollback failed ERP release")
    finalize = deploy.split("- name: Finalize production release", 1)[1].split("\n      - name:", 1)[0]
    assert "' finish " in finalize


def test_deploy_recovers_interrupted_services_before_taking_the_backup() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    recovery = deploy.split("- name: Ensure ERP services are running", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert deploy.index("Ensure ERP services are running") < deploy.index(
        "Prepare production rollback point"
    )
    assert "docker compose -f compose.custom.yaml up -d" in recovery
    assert "backend websocket queue-short queue-long scheduler frontend" in recovery
    assert "docker compose -f compose.custom.yaml ps -q backend" in recovery
    assert "docker compose -f compose.custom.yaml logs --tail=200 backend" in recovery


def test_database_rollback_restores_maintenance_even_when_asset_verification_fails() -> None:
    release = (
        WORKFLOW_PATH.parents[1] / "scripts" / "manage_material_ai_release.sh"
    ).read_text(encoding="utf-8")
    rollback = release.split("rollback_release()", 1)[1].split(
        "rollback_safe_release()", 1
    )[0]

    assert "asset_status=0" in rollback
    assert "sync_and_verify_assets_only || asset_status=$?" in rollback
    assert rollback.index("sync_and_verify_assets_only || asset_status=$?") < rollback.index(
        "restore_maintenance_mode"
    )
    assert rollback.index("restore_maintenance_mode") < rollback.index(
        'return "$asset_status"'
    )


def test_failed_release_maintenance_can_be_recovered_without_database_rollback() -> None:
    release = (
        WORKFLOW_PATH.parents[1] / "scripts" / "manage_material_ai_release.sh"
    ).read_text(encoding="utf-8")
    recovery = release.split("recover_maintenance_after_rollback()", 1)[1].split(
        "rollback_release()", 1
    )[0]

    assert "database_is_committed" in recovery
    assert "current_maintenance_state" in recovery
    assert 'if [ "$state" != "on" ]' in recovery
    assert "restore_maintenance_mode" in recovery
    assert "recover-maintenance) recover_maintenance_after_rollback" in release


def test_login_smoke_waits_for_backend_without_retrying_expected_maintenance_status() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    check = workflow.split("- name: Check online maintenance response", 1)[1].split("\n      - name:", 1)[0]

    assert "--retry" not in check
    assert "seq 1 19" in check
    assert "sleep 5" in check
    assert "SECONDS + 90" in check
    assert "--max-time 5" in check


@pytest.mark.parametrize(
    "curl_exit,http_status,expected_exit",
    [(0, "503", 0), (0, "200", 0), (0, "502", 1), (23, "503", 1)],
)
def test_maintenance_http_gate_accepts_only_successful_expected_responses(
    tmp_path, curl_exit, http_status, expected_exit,
) -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    check = workflow.split("- name: Check online maintenance response", 1)[1].split("\n      - name:", 1)[0]
    shell = textwrap.dedent(check.split("run: |\n", 1)[1])
    curl = tmp_path / "curl"
    curl.write_text('#!/bin/sh\nprintf "%s" "$MOCK_HTTP_STATUS"\nexit "$MOCK_CURL_EXIT"\n')
    curl.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    environment = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
                       MOCK_HTTP_STATUS=http_status, MOCK_CURL_EXIT=str(curl_exit))

    result = subprocess.run(["bash", "-e", "-c", shell], env=environment,
                            capture_output=True, text=True, timeout=10)

    assert result.returncode == expected_exit


def test_deepseek_preflight_reads_secret_from_environment_without_printing_it() -> None:
    script = (WORKFLOW_PATH.parent.parent / "scripts" / "preflight_deepseek.py").read_text(encoding="utf-8")
    assert 'os.environ.get("DEEPSEEK_API_KEY"' in script
    assert '"Authorization": f"Bearer {api_key}"' in script
    assert "print(api_key)" not in script


def test_deepseek_preflight_disables_thinking_and_retries_empty_json_output() -> None:
    script = (WORKFLOW_PATH.parent.parent / "scripts" / "preflight_deepseek.py").read_text(encoding="utf-8")

    assert '"thinking": {"type": "disabled"}' in script
    assert '"response_format": {"type": "json_object"}' in script
    assert '"max_tokens": 128' in script
    assert "MAX_ATTEMPTS = 3" in script
    assert "DEEPSEEK_VISION_MODEL" in script
    assert "deepseek-v4-flash-vision-exp" in script
    assert '"type": "image_url"' in script
    vision_payload = script.split("def _vision_request_payload", 1)[1].split("def _call", 1)[0]
    assert '"thinking"' not in vision_payload
    assert '"response_format"' not in vision_payload


def test_asset_script_is_uploaded_and_executed_as_a_remote_file():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy_block = workflow.split("\n  deploy:\n", maxsplit=1)[1]

    assert "< .github/scripts/sync_and_verify_assets.sh" not in deploy_block
    assert (
        'remote_asset_script="/tmp/sync_and_verify_assets-'
        '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.sh"'
        in deploy_block
    )
    assert "scp -o BatchMode=yes -o StrictHostKeyChecking=yes" in deploy_block
    assert "bash '$remote_asset_script'" in deploy_block
    assert "rm -f '/tmp/manage_material_ai_release-" in deploy_block
    assert "'/tmp/sync_and_verify_assets-" in deploy_block


def test_container_restart_scripts_reuse_asset_sync_gate() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy_block = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    release = (WORKFLOW_PATH.parents[1] / "scripts" / "manage_material_ai_release.sh").read_text(encoding="utf-8")
    asset_script = (WORKFLOW_PATH.parents[1] / "scripts" / "sync_and_verify_assets.sh").read_text(encoding="utf-8")

    assert "ASSET_SYNC_SCRIPT='/tmp/sync_and_verify_assets-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.sh'" in deploy_block
    assert deploy_block.index("sync_and_verify_assets.sh") < deploy_block.index("Install document parsing runtime")
    assert 'script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in release
    assert 'asset_sync_script="$script_dir/sync_and_verify_assets.sh"' in release
    assert "VERIFY_APP_RELEASE=0" in release
    assert 'VERIFY_APP_RELEASE="${VERIFY_APP_RELEASE:-1}"' in asset_script
    assert 'VERIFY_INVENTORY_ASSETS="$VERIFY_APP_RELEASE"' in asset_script
    assert 'os.environ["VERIFY_INVENTORY_ASSETS"] == "1"' in asset_script
    assert "Skip overseas costing application release verification" in asset_script


def test_asset_script_rejects_stale_overseas_costing_release():
    script_path = WORKFLOW_PATH.parents[1] / "scripts" / "sync_and_verify_assets.sh"
    script = script_path.read_text(encoding="utf-8")

    assert "get_batch_dingtalk_approval_detail" in script
    assert "renderDingtalkApprovalTab" in script


def test_release_marker_switches_only_after_asset_verification() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]

    assets = deploy.index("Synchronize and verify frontend assets")
    marker = deploy.index("Publish workbench release marker")
    health = deploy.index("Check online maintenance response")
    finalize = deploy.index("Finalize production release")
    assert assets < marker < health < finalize
    assert "overseas_costing_release_id" in deploy
    assert "${GITHUB_SHA}" in deploy


def test_image_reclaim_keeps_the_image_compose_declares() -> None:
    """回收镜像不能删掉 compose 声明、而容器还没引用的那一个。

    2026-09-28 的现场：compose 刚被改成新 tag、容器还没重建，那条内联的
    `docker image prune --all` 就把新 tag 的镜像当垃圾删了，紧接着的 preflight
    报 docker 的 "No such image"，整条流水线什么都没发出去。
    """
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", maxsplit=1)[1]
    script = (WORKFLOW_PATH.parents[1] / "scripts" / "reclaim_docker_space.sh").read_text(encoding="utf-8")

    assert "docker builder prune --all --force && docker image prune --all --force" not in deploy
    assert 'remote_reclaim_script="/tmp/reclaim_docker_space-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.sh"' in deploy
    assert "BASE_IMAGE_SCRIPT='$remote_resolve_script' bash '$remote_reclaim_script'" in deploy
    assert "'/tmp/reclaim_docker_space-" in deploy
    # preflight 要用这个镜像起容器，回收必须排在它前面。
    assert deploy.index("Reclaim unused Docker build cache") < deploy.index("Preflight document parsing runtime")

    assert "ocw-deploy-image-guard" in script
    assert 'docker create --name "$guard_container" "$base_image"' in script
    assert "trap remove_guard EXIT" in script
    # 唯一真源仍是 compose：脚本自己解析，不许硬编码 tag。
    assert 'base_image="$(resolve_base_image "$compose_file"' in script
    # 解析不出 compose 声明的镜像时只回收悬空层，绝不赌 `--all`。
    assert "docker image prune --all --force" in script
    assert "docker image prune --force" in script
