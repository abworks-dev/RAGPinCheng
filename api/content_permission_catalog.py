from __future__ import annotations

from dataclasses import dataclass


CONTENT_PERMISSION_CATALOG_VERSION = 3


@dataclass(frozen=True, slots=True)
class ContentPermissionDefinition:
    key: str
    domain: str
    domain_label: str
    label: str
    description: str
    dependencies: tuple[str, ...] = ()


CONTENT_PERMISSION_DEFINITIONS = (
    ContentPermissionDefinition(
        "workspace.view", "access", "入口与查看", "进入资料工作台", "进入资料管理工作台。"
    ),
    ContentPermissionDefinition(
        "item.view", "access", "入口与查看", "查看资料", "查看资料列表、详情和预览。", ("workspace.view",)
    ),
    ContentPermissionDefinition(
        "item.download", "access", "入口与查看", "下载资料", "下载单份资料或批量打包下载。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "category.view", "access", "入口与查看", "查看分类", "查看资料分类树和完整路径。", ("workspace.view",)
    ),
    ContentPermissionDefinition(
        "item.upload", "organize", "资料整理", "上传资料", "上传文件并创建资料草稿。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "item.submit", "organize", "资料整理", "提交确认", "将草稿或退回资料提交确认。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "item.move_draft", "organize", "资料整理", "移动草稿", "移动草稿或退回状态的资料。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "item.archive_draft", "organize", "资料整理", "归档草稿", "将草稿或退回资料移入回收站。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "item.review", "review", "确认流程", "确认与退回", "确认或退回待确认资料。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "item.move_review", "review", "确认流程", "移动待确认资料", "移动待确认状态的资料。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "item.publish", "publish", "发布流程", "发布资料", "发布或重新发布已确认资料。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "item.archive_published", "publish", "发布流程", "下架正式资料", "将已确认、发布失败或已发布资料移入回收站。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "trash.view", "trash", "回收站", "查看回收站", "查看和搜索已归档资料。",
        ("workspace.view", "item.view"),
    ),
    ContentPermissionDefinition(
        "trash.restore", "trash", "回收站", "恢复资料", "从回收站恢复资料。",
        ("workspace.view", "item.view", "trash.view"),
    ),
    ContentPermissionDefinition(
        "category.manage", "category", "分类与目录", "维护分类", "新增、修改、启用或停用资料分类。",
        ("workspace.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "folder.request", "category", "分类与目录", "申请目录", "提交子目录创建申请。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "folder.review", "category", "分类与目录", "审批目录", "查看、批准或退回目录申请。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "import.server", "operations", "导入与索引", "服务器导入", "执行受控的服务器批次导入。",
        ("workspace.view", "item.view", "category.view"),
    ),
    ContentPermissionDefinition(
        "index.view", "operations", "导入与索引", "查看索引任务", "查看发布处理状态、失败原因和历史尝试。",
        ("workspace.view", "item.view", "category.view"),
    ),
)

CONTENT_PERMISSION_BY_KEY = {item.key: item for item in CONTENT_PERMISSION_DEFINITIONS}
CONTENT_PERMISSIONS = frozenset(CONTENT_PERMISSION_BY_KEY)

LEGACY_CONTENT_PERMISSION_MAP = {
    "organize": frozenset({
        "workspace.view", "item.view", "item.download", "category.view", "item.upload", "item.submit",
        "item.move_draft", "item.archive_draft", "folder.request",
    }),
    "review": frozenset({
        "workspace.view", "item.view", "item.download", "category.view", "item.review", "item.move_review",
        "folder.review", "trash.view", "trash.restore",
    }),
    "publish": frozenset({
        "workspace.view", "item.view", "item.download", "category.view", "item.publish",
        "item.archive_published", "trash.view", "index.view",
    }),
    "manage_categories": frozenset({
        "workspace.view", "item.view", "item.download", "category.view", "category.manage", "folder.review",
    }),
    "import_server": frozenset({
        "workspace.view", "item.view", "item.download", "category.view", "import.server",
    }),
}

LEGACY_SYSTEM_CONTENT_PERMISSION_GROUPS = {
    "member": ("普通成员", frozenset()),
    "bim_engineer": ("BIM工程师", frozenset({"organize"})),
    "content_owner": ("资料负责人", frozenset({"review"})),
    "system_admin": (
        "系统管理员",
        frozenset({"organize", "review", "publish", "manage_categories", "import_server"}),
    ),
}

# Schema 11 databases must validate against the pre-download permission catalog
# before Schema 12 can add the new node.
CONTENT_PERMISSION_V2_SYSTEM_CONTENT_PERMISSION_GROUPS = {
    "member": ("普通成员", frozenset()),
    "viewer": (
        "资料浏览者",
        frozenset({"workspace.view", "item.view", "category.view"}),
    ),
    "bim_engineer": (
        "BIM工程师",
        frozenset({
            "workspace.view", "item.view", "category.view", "item.upload", "item.submit",
            "item.move_draft", "item.archive_draft", "folder.request",
        }),
    ),
    "content_owner": (
        "资料负责人",
        frozenset({
            "workspace.view", "item.view", "category.view", "item.review", "item.move_review",
            "folder.review", "trash.view", "trash.restore",
        }),
    ),
    "publisher": (
        "发布负责人",
        frozenset({
            "workspace.view", "item.view", "category.view", "item.publish",
            "item.archive_published", "trash.view", "index.view",
        }),
    ),
    "category_admin": (
        "分类管理员",
        frozenset({"workspace.view", "item.view", "category.view", "category.manage", "folder.review"}),
    ),
    "system_admin": (
        "系统管理员",
        frozenset({definition.key for definition in CONTENT_PERMISSION_DEFINITIONS if definition.key != "item.download"}),
    ),
}

SYSTEM_CONTENT_PERMISSION_GROUPS = {
    "member": ("普通成员", frozenset()),
    "viewer": (
        "资料浏览者",
        frozenset({"workspace.view", "item.view", "item.download", "category.view"}),
    ),
    "bim_engineer": ("BIM工程师", LEGACY_CONTENT_PERMISSION_MAP["organize"]),
    "content_owner": ("资料负责人", LEGACY_CONTENT_PERMISSION_MAP["review"]),
    "publisher": ("发布负责人", LEGACY_CONTENT_PERMISSION_MAP["publish"]),
    "category_admin": (
        "分类管理员",
        frozenset({
            "workspace.view", "item.view", "item.download", "category.view",
            "category.manage", "folder.review",
        }),
    ),
    "system_admin": ("系统管理员", CONTENT_PERMISSIONS),
}


def missing_content_permission_dependencies(permissions: set[str]) -> dict[str, tuple[str, ...]]:
    missing: dict[str, tuple[str, ...]] = {}
    for permission in sorted(permissions):
        definition = CONTENT_PERMISSION_BY_KEY.get(permission)
        if definition is None:
            continue
        absent = tuple(item for item in definition.dependencies if item not in permissions)
        if absent:
            missing[permission] = absent
    return missing
