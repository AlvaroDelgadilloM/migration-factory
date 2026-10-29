{
    'name': 'Legacy Sales Custom',
    'version': '12.0.1.0.0',
    'category': 'Sales',
    'depends': ['sale_management', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',
        'data/demo.xml',
    ],
    'installable': True,
}
