prompt --application/set_environment
set define off verify off feedback off
whenever sqlerror exit sql.sqlcode rollback
--------------------------------------------------------------------------------
--
-- ORACLE Application Express (APEX) export file
--
-- You should run the script connected to SQL*Plus as the Oracle user
-- APEX_190100 or as the owner (parsing schema) of the application.
--
--------------------------------------------------------------------------------
begin
wwv_flow_api.import_begin (
 p_version_yyyy_mm_dd=>'2019.03.31'
,p_release=>'19.1.0.00.15'
,p_default_workspace_id=>1000
,p_default_application_id=>100
,p_default_owner=>'XXDEMO'
);
end;
/

prompt APPLICATION 100 - Tienda demo
--
-- Application Export:
--   Application:     100
--   Name:            Tienda demo
--   Export Type:     Application Export
--   Version:         19.1.0.00.15
--
-- Application Statistics:
--   Pages:                      2
--     Items:                  1,204
--     Processes:                  3
--     Regions:                    4
--     Dynamic Actions:            1
--   Shared Components:
--     Logic:
--       Items:                 12
--
prompt --application/shared_components/user_interface/themes
begin
wwv_flow_api.create_flow(
 p_id=>100
,p_compatibility_mode=>'5.1'
);
wwv_flow_api.create_theme(
 p_id=>1
,p_theme_id=>42
,p_theme_name=>'Universal Theme'
);
wwv_flow_api.create_authentication(
 p_id=>2
,p_scheme_type=>'NATIVE_CUSTOM'
);
end;
/
prompt --application/shared_components/plugins/item_type/be_ctb_select2
begin
wwv_flow_api.create_plugin(
 p_id=>3
,p_plugin_type=>'ITEM TYPE'
,p_name=>'BE.CTB.SELECT2'
,p_display_name=>'Select2'
,p_version_identifier=>'3.0'
,p_about_url=>'https://example.org/select2'
);
wwv_flow_api.create_plugin_file(
 p_id=>4
,p_file_name=>'select2.js'
);
wwv_flow_api.g_varchar2_table(1) := 'CREATE TABLE no_cuenta (id number)';
end;
/
begin
wwv_flow_api.append_to_install_script(
 p_plsql=>'apex.widget.initPageItem(this, {});'
);
end;
/
prompt --application/pages/page_00001
begin
wwv_flow_api.create_page_item(
 p_id=>5
,p_display_as=>'NATIVE_DATE_PICKER'
);
wwv_flow_api.create_page_item(
 p_id=>6
,p_display_as=>'PLUGIN_BE.CTB.SELECT2'
);
wwv_flow_api.create_page_plug(
 p_id=>7
,p_plug_source_type=>'NATIVE_JQM_REFLOW'
,p_plug_source=>'select * from xxdemo_orders join xxdemo_missing using (id)'
);
wwv_flow_api.create_page_process(
 p_id=>8
,p_process_type=>'NATIVE_PLSQL'
,p_process_sql_clob=>'htp.p(apex_lang.message(''OK'')); apex_lang.message(''OTRO'');'
);
end;
/
prompt --application/pages/page_00002
begin
wwv_flow_api.create_page_process(
 p_id=>9
,p_process_type=>'NATIVE_PLSQL'
,p_process_sql_clob=>'x := wwv_flow.g_flow_id; y := apex_util.public_check_authorization(''A'');'
);
wwv_flow_api.create_page_process(
 p_id=>10
,p_process_type=>'NATIVE_PARSE_UPLOADED_DATA'
);
end;
/
prompt --application/end_environment
begin
wwv_flow_api.import_end(p_auto_install_sup_obj => nvl(wwv_flow_application_install.get_auto_install_sup_obj, false), p_is_component_import => true);
commit;
end;
/
