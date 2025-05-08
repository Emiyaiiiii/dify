import uuid
from flask_restful import Resource, inputs, marshal, marshal_with, reqparse # type: ignore
from controllers.common import fields
from controllers.service_api import api
from controllers.service_api.app.error import AppUnavailableError
from controllers.service_api.wraps import validate_app_token
from controllers.service_api.wraps import AppApiResource
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from models.model import App, AppMode
from services.app_service import AppService
from werkzeug.exceptions import BadRequest, Forbidden, abort
from fields.app_fields import (
    app_pagination_fields,
)
from flask_login import current_user  # type: ignore
from typing import Any
import flask_restful  # type: ignore
from werkzeug.exceptions import Forbidden
from extensions.ext_database import db
from libs.helper import TimestampField
from libs.login import login_required
from models.model import ApiToken, App

from ...console.wraps import account_initialization_required, setup_required

ALLOW_CREATE_APP_MODES = ["agent-chat"]

api_key_fields = {
    "id": fields.fields.String,
    "type": fields.fields.String,
    "token": fields.fields.String,
    "last_used_at": TimestampField,
    "created_at": TimestampField,
}
api_key_list = {"data": fields.fields.List(fields.fields.Nested(api_key_fields), attribute="items")}

def _get_resource(resource_id, tenant_id, resource_model):
    resource = resource_model.query.filter_by(id=resource_id, tenant_id=tenant_id).first()

    if resource is None:
        flask_restful.abort(404, message=f"{resource_model.__name__} not found.")

    return resource


class AppParameterApi(Resource):
    """Resource for app variables."""

    @validate_app_token
    @marshal_with(fields.parameters_fields)
    def get(self, app_model: App):
        """Retrieve app parameters."""
        if app_model.mode in {AppMode.ADVANCED_CHAT.value, AppMode.WORKFLOW.value}:
            workflow = app_model.workflow
            if workflow is None:
                raise AppUnavailableError()

            features_dict = workflow.features_dict
            user_input_form = workflow.user_input_form(to_old_structure=True)
        else:
            app_model_config = app_model.app_model_config
            if app_model_config is None:
                raise AppUnavailableError()

            features_dict = app_model_config.to_dict()

            user_input_form = features_dict.get("user_input_form", [])

        return get_parameters_from_feature_dict(features_dict=features_dict, user_input_form=user_input_form)


class AppMetaApi(Resource):
    @validate_app_token
    def get(self, app_model: App):
        """Get app meta"""
        return AppService().get_app_meta(app_model)


class AppInfoApi(Resource):
    @validate_app_token
    def get(self, app_model: App):
        """Get app information"""
        tags = [tag.name for tag in app_model.tags]
        return {"name": app_model.name, "description": app_model.description, "tags": tags}
    

class AppListApi(AppApiResource):
    def get(self):
        """Get app list"""

        def uuid_list(value):
            try:
                return [str(uuid.UUID(v)) for v in value.split(",")]
            except ValueError:
                abort(400, message="Invalid UUID format in tag_ids.")

        parser = reqparse.RequestParser()
        parser.add_argument("page", type=inputs.int_range(1, 99999), required=False, default=1, location="args")
        parser.add_argument("limit", type=inputs.int_range(1, 100), required=False, default=20, location="args")
        parser.add_argument(
            "mode",
            type=str,
            choices=["agent-chat"],
            default="all",
            location="args",
            required=False,
        )
        parser.add_argument("name", type=str, location="args", required=False)
        parser.add_argument("tag_ids", type=uuid_list, location="args", required=False)
        parser.add_argument("is_created_by_me", type=inputs.boolean, location="args", required=False)

        args = parser.parse_args()

        # get app list
        app_service = AppService()
        app_pagination = app_service.get_paginate_apps(current_user.id, current_user.current_tenant_id, args)
        if not app_pagination:
            return {"data": [], "total": 0, "page": 1, "limit": 20, "has_more": False}

        return marshal(app_pagination, app_pagination_fields)

    def post(self):
        """Create app"""
        parser = reqparse.RequestParser()
        parser.add_argument("name", type=str, required=True, location="json")
        parser.add_argument("description", type=str, location="json")
        parser.add_argument("mode", type=str, choices=ALLOW_CREATE_APP_MODES, location="json")
        parser.add_argument("icon_type", type=str, location="json")
        parser.add_argument("icon", type=str, location="json")
        parser.add_argument("icon_background", type=str, location="json")
        args = parser.parse_args()

        # The role of the current user in the ta table must be admin, owner, or editor
        if not current_user.is_editor:
            raise Forbidden()

        if "mode" not in args or args["mode"] is None:
            raise BadRequest("mode is required")

        app_service = AppService()
        app = app_service.create_app(current_user.current_tenant_id, args, current_user)

        return app, 201


class AppApi(AppApiResource):
    def get(self, app_model):
        """Get app detail"""
        app_service = AppService()

        app_model = app_service.get_app(app_model)

        return app_model

    def put(self, app_model):
        """Update app"""
        # The role of the current user in the ta table must be admin, owner, or editor
        if not current_user.is_editor:
            raise Forbidden()

        parser = reqparse.RequestParser()
        parser.add_argument("name", type=str, required=True, nullable=False, location="json")
        parser.add_argument("description", type=str, location="json")
        parser.add_argument("icon_type", type=str, location="json")
        parser.add_argument("icon", type=str, location="json")
        parser.add_argument("icon_background", type=str, location="json")
        parser.add_argument("max_active_requests", type=int, location="json")
        parser.add_argument("use_icon_as_answer_icon", type=bool, location="json")
        args = parser.parse_args()

        app_service = AppService()
        app_model = app_service.update_app(app_model, args)

        return app_model

    def delete(self, app_model):
        """Delete app"""
        # The role of the current user in the ta table must be admin, owner, or editor
        if not current_user.is_editor:
            raise Forbidden()

        app_service = AppService()
        app_service.delete_app(app_model)

        return {"result": "success"}, 204
    

class BaseApiKeyListResource(AppApiResource):

    resource_type: str | None = None
    resource_model: Any = None
    resource_id_field: str | None = None
    token_prefix: str | None = None
    max_keys = 10

    @marshal_with(api_key_list)
    def get(self, resource_id):
        assert self.resource_id_field is not None, "resource_id_field must be set"
        resource_id = str(resource_id)
        _get_resource(resource_id, current_user.current_tenant_id, self.resource_model)
        keys = (
            db.session.query(ApiToken)
            .filter(ApiToken.type == self.resource_type, getattr(ApiToken, self.resource_id_field) == resource_id)
            .all()
        )
        return {"items": keys}

    @marshal_with(api_key_fields)
    def post(self, resource_id):
        assert self.resource_id_field is not None, "resource_id_field must be set"
        resource_id = str(resource_id)
        _get_resource(resource_id, current_user.current_tenant_id, self.resource_model)
        if not current_user.is_editor:
            raise Forbidden()

        current_key_count = (
            db.session.query(ApiToken)
            .filter(ApiToken.type == self.resource_type, getattr(ApiToken, self.resource_id_field) == resource_id)
            .count()
        )

        if current_key_count >= self.max_keys:
            flask_restful.abort(
                400,
                message=f"Cannot create more than {self.max_keys} API keys for this resource type.",
                code="max_keys_exceeded",
            )

        key = ApiToken.generate_api_key(self.token_prefix, 24)
        api_token = ApiToken()
        setattr(api_token, self.resource_id_field, resource_id)
        api_token.tenant_id = current_user.current_tenant_id
        api_token.token = key
        api_token.type = self.resource_type
        db.session.add(api_token)
        db.session.commit()
        return api_token, 201


class BaseApiKeyResource(AppApiResource):

    resource_type: str | None = None
    resource_model: Any = None
    resource_id_field: str | None = None

    def delete(self, resource_id, api_key_id):
        assert self.resource_id_field is not None, "resource_id_field must be set"
        resource_id = str(resource_id)
        api_key_id = str(api_key_id)
        _get_resource(resource_id, current_user.current_tenant_id, self.resource_model)

        # The role of the current user in the ta table must be admin or owner
        if not current_user.is_admin_or_owner:
            raise Forbidden()

        key = (
            db.session.query(ApiToken)
            .filter(
                getattr(ApiToken, self.resource_id_field) == resource_id,
                ApiToken.type == self.resource_type,
                ApiToken.id == api_key_id,
            )
            .first()
        )

        if key is None:
            flask_restful.abort(404, message="API key not found")

        db.session.query(ApiToken).filter(ApiToken.id == api_key_id).delete()
        db.session.commit()

        return {"result": "success"}, 204


class AppApiKeyListResource(BaseApiKeyListResource):
    def after_request(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp

    resource_type = "app"
    resource_model = App
    resource_id_field = "app_id"
    token_prefix = "app-"


class AppApiKeyResource(BaseApiKeyResource):
    def after_request(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp

    resource_type = "app"
    resource_model = App
    resource_id_field = "app_id"
    

api.add_resource(AppParameterApi, "/parameters")
api.add_resource(AppMetaApi, "/meta")
api.add_resource(AppInfoApi, "/info")


api.add_resource(AppListApi, "/apps")
api.add_resource(AppApi, "/apps/<uuid:app_id>")


api.add_resource(AppApiKeyListResource, "/apps/<uuid:resource_id>/api-keys")
api.add_resource(AppApiKeyResource, "/apps/<uuid:resource_id>/api-keys/<uuid:api_key_id>")
