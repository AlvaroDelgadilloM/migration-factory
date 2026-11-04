<?xml version="1.0" encoding="UTF-8"?>
<!-- Reporte de inventario: agrega el valor de cada producto y el total, y marca los que están sin existencias. -->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:output method="xml" indent="no" encoding="UTF-8"/>
    <xsl:template match="/inventario">
        <reporteInventario productos="{count(producto)}" valorTotal="{format-number(sum(producto/@valor), '0.00')}">
            <xsl:for-each select="producto">
                <xsl:sort select="@id" data-type="number"/>
                <producto id="{@id}" existencias="{@existencias}" valor="{format-number(@valor, '0.00')}">
                    <xsl:if test="@existencias = 0"><xsl:attribute name="agotado">true</xsl:attribute></xsl:if>
                    <xsl:value-of select="@nombre"/>
                </producto>
            </xsl:for-each>
        </reporteInventario>
    </xsl:template>
</xsl:stylesheet>
