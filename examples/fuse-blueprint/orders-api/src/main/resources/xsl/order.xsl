<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
	<xsl:param name="orderId" />
	<xsl:template match="/">
		<order><id><xsl:value-of select="$orderId" /></id></order>
	</xsl:template>
</xsl:stylesheet>
