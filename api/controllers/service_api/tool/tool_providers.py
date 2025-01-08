from libs.login import current_user
from flask_restful import reqparse  # type: ignore
from werkzeug.exceptions import Forbidden

from controllers.service_api import api
from controllers.service_api.wraps import ToolApiResource
from core.model_runtime.utils.encoders import jsonable_encoder
from services.tools.api_tools_manage_service import ApiToolManageService
from services.tools.tool_labels_service import ToolLabelsService
from services.tools.tools_manage_service import ToolCommonService


class ToolProviderListApi(ToolApiResource):
    def get(self):
        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        req = reqparse.RequestParser()
        req.add_argument(
            "type",
            type=str,
            choices=["builtin", "model", "api", "workflow"],
            required=False,
            nullable=True,
            location="args",
        )
        args = req.parse_args()

        return ToolCommonService.list_tool_providers(user_id, tenant_id, args.get("type", None))


class ToolApiProviderAddApi(ToolApiResource):
    def post(self):
        if not current_user.is_admin_or_owner:
            raise Forbidden()

        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        parser = reqparse.RequestParser()
        parser.add_argument("credentials", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("schema_type", type=str, required=True, nullable=False, location="json")
        parser.add_argument("schema", type=str, required=True, nullable=False, location="json")
        parser.add_argument("provider", type=str, required=True, nullable=False, location="json")
        parser.add_argument("icon", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("privacy_policy", type=str, required=False, nullable=True, location="json")
        parser.add_argument("labels", type=list[str], required=False, nullable=True, location="json", default=[])
        parser.add_argument("custom_disclaimer", type=str, required=False, nullable=True, location="json")

        args = parser.parse_args()

        return ApiToolManageService.create_api_tool_provider(
            user_id,
            tenant_id,
            args["provider"],
            args["icon"],
            args["credentials"],
            args["schema_type"],
            args["schema"],
            args.get("privacy_policy", ""),
            args.get("custom_disclaimer", ""),
            args.get("labels", []),
        )


class ToolApiProviderGetRemoteSchemaApi(ToolApiResource):
    def get(self):
        parser = reqparse.RequestParser()

        parser.add_argument("url", type=str, required=True, nullable=False, location="args")

        args = parser.parse_args()

        return ApiToolManageService.get_api_tool_provider_remote_schema(
            current_user.id,
            current_user.current_tenant_id,
            args["url"],
        )


class ToolApiProviderListToolsApi(ToolApiResource):
    def get(self):
        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        parser = reqparse.RequestParser()

        parser.add_argument("provider", type=str, required=True, nullable=False, location="args")

        args = parser.parse_args()

        return jsonable_encoder(
            ApiToolManageService.list_api_tool_provider_tools(
                user_id,
                tenant_id,
                args["provider"],
            )
        )


class ToolApiProviderUpdateApi(ToolApiResource):
    def post(self):
        if not current_user.is_admin_or_owner:
            raise Forbidden()

        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        parser = reqparse.RequestParser()
        parser.add_argument("credentials", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("schema_type", type=str, required=True, nullable=False, location="json")
        parser.add_argument("schema", type=str, required=True, nullable=False, location="json")
        parser.add_argument("provider", type=str, required=True, nullable=False, location="json")
        parser.add_argument("original_provider", type=str, required=True, nullable=False, location="json")
        parser.add_argument("icon", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("privacy_policy", type=str, required=True, nullable=True, location="json")
        parser.add_argument("labels", type=list[str], required=False, nullable=True, location="json")
        parser.add_argument("custom_disclaimer", type=str, required=True, nullable=True, location="json")

        args = parser.parse_args()

        return ApiToolManageService.update_api_tool_provider(
            user_id,
            tenant_id,
            args["provider"],
            args["original_provider"],
            args["icon"],
            args["credentials"],
            args["schema_type"],
            args["schema"],
            args["privacy_policy"],
            args["custom_disclaimer"],
            args.get("labels", []),
        )


class ToolApiProviderDeleteApi(ToolApiResource):
    def post(self):
        if not current_user.is_admin_or_owner:
            raise Forbidden()

        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        parser = reqparse.RequestParser()

        parser.add_argument("provider", type=str, required=True, nullable=False, location="json")

        args = parser.parse_args()

        return ApiToolManageService.delete_api_tool_provider(
            user_id,
            tenant_id,
            args["provider"],
        )


class ToolApiProviderGetApi(ToolApiResource):
    def get(self):
        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        parser = reqparse.RequestParser()

        parser.add_argument("provider", type=str, required=True, nullable=False, location="args")

        args = parser.parse_args()

        return ApiToolManageService.get_api_tool_provider(
            user_id,
            tenant_id,
            args["provider"],
        )


class ToolApiProviderSchemaApi(ToolApiResource):
    def post(self):
        parser = reqparse.RequestParser()

        parser.add_argument("schema", type=str, required=True, nullable=False, location="json")

        args = parser.parse_args()

        return ApiToolManageService.parser_api_schema(
            schema=args["schema"],
        )


class ToolApiProviderPreviousTestApi(ToolApiResource):
    def post(self):
        parser = reqparse.RequestParser()

        parser.add_argument("tool_name", type=str, required=True, nullable=False, location="json")
        parser.add_argument("provider_name", type=str, required=False, nullable=False, location="json")
        parser.add_argument("credentials", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("parameters", type=dict, required=True, nullable=False, location="json")
        parser.add_argument("schema_type", type=str, required=True, nullable=False, location="json")
        parser.add_argument("schema", type=str, required=True, nullable=False, location="json")

        args = parser.parse_args()

        return ApiToolManageService.test_api_tool_preview(
            current_user.current_tenant_id,
            args["provider_name"] or "",
            args["tool_name"],
            args["credentials"],
            args["parameters"],
            args["schema_type"],
            args["schema"],
        )


class ToolApiListApi(ToolApiResource):
    def get(self):
        user_id = current_user.id
        tenant_id = current_user.current_tenant_id

        return jsonable_encoder(
            [
                provider.to_dict()
                for provider in ApiToolManageService.list_api_tools(
                    user_id,
                    tenant_id,
                )
            ]
        )


class ToolLabelsApi(ToolApiResource):
    def get(self):
        return jsonable_encoder(ToolLabelsService.list_tool_labels())


# # tool provider
api.add_resource(ToolProviderListApi, "/tool-providers")

# api tool provider
api.add_resource(ToolApiProviderAddApi, "/tool-provider/api/add")
api.add_resource(ToolApiProviderGetRemoteSchemaApi, "/tool-provider/api/remote")
api.add_resource(ToolApiProviderListToolsApi, "/tool-provider/api/tools")
api.add_resource(ToolApiProviderUpdateApi, "/tool-provider/api/update")
api.add_resource(ToolApiProviderDeleteApi, "/tool-provider/api/delete")
api.add_resource(ToolApiProviderGetApi, "/tool-provider/api/get")
api.add_resource(ToolApiProviderSchemaApi, "/tool-provider/api/schema")
api.add_resource(ToolApiProviderPreviousTestApi, "/tool-provider/api/test/pre")

api.add_resource(ToolApiListApi, "/tools/api")

api.add_resource(ToolLabelsApi, "/tool-labels")
