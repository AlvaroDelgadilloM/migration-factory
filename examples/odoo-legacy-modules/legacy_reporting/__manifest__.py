{
    'name': 'Legacy SQL Reporting',
    'version': '12.0.1.0.0',
    'depends': ['sale_management', 'legacy_sales_custom'],
    'data': ['security/ir.model.access.csv', 'views/legacy_report_views.xml'],
    'installable': True,
}
