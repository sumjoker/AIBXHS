-- =====================================================
-- 迁移脚本：添加库存店铺名映射字段
-- 用于解决数据库店铺名与库存数据店铺名不一致的问题
-- =====================================================

-- 1. 添加 inventory_name 字段到 stores 表
ALTER TABLE `stores`
ADD COLUMN IF NOT EXISTS `inventory_name` VARCHAR(100) DEFAULT NULL COMMENT '库存数据中的店铺名别名' AFTER `department_id`,
ADD INDEX IF NOT EXISTS `idx_inventory_name` (`inventory_name`);

-- 2. 插入店铺映射数据
-- 云南金顺公司：JeVenis-国家
-- B账号账户管理：LaVenty-US、LaVenty-US-BR(美国、巴西)
-- C账号账户管理：Roaring-US、Roaring-BR(美国、巴西)
-- D账号账户管理: D-USA-US、D-USA-CA(美国、加拿大)
-- E账号账户管理: E-DE(德国)
-- F账号账户管理：F-DE、F-FR、F-IT、F-ES、F-NL、F-SE、F-PL、F-BE(德国、法国、意大利、西班牙、荷兰、瑞典、波兰、比利时)
-- G站点紫鸟账号: G-USA、G-CA、G-MX(美国、加拿大、墨西哥)

-- 注意：请根据实际情况更新以下INSERT语句中的店铺ID
-- 先查询现有店铺
-- SELECT id, name, site FROM stores WHERE tenant_id = 1;

-- 示例：更新云南金顺公司店铺
-- UPDATE stores SET inventory_name = 'JeVenis-US' WHERE name = '云南金顺公司' AND site = 'US';
-- UPDATE stores SET inventory_name = 'JeVenis-CA' WHERE name = '云南金顺公司' AND site = 'CA';
-- UPDATE stores SET inventory_name = 'JeVenis-MX' WHERE name = '云南金顺公司' AND site = 'MX';

-- 示例：更新B账号店铺
-- UPDATE stores SET inventory_name = 'LaVenty-US' WHERE name LIKE '%B账号%' AND site = 'US';
-- UPDATE stores SET inventory_name = 'LaVenty-US-BR' WHERE name LIKE '%B账号%' AND site = 'BR';

-- 示例：更新C账号店铺
-- UPDATE stores SET inventory_name = 'Roaring-US' WHERE name LIKE '%C账号%' AND site = 'US';
-- UPDATE stores SET inventory_name = 'Roaring-BR' WHERE name LIKE '%C账号%' AND site = 'BR';

-- 示例：更新D账号店铺
-- UPDATE stores SET inventory_name = 'D-USA-US' WHERE name LIKE '%D账号%' AND site = 'US';
-- UPDATE stores SET inventory_name = 'D-USA-CA' WHERE name LIKE '%D账号%' AND site = 'CA';

-- 示例：更新E账号店铺
-- UPDATE stores SET inventory_name = 'E-DE' WHERE name LIKE '%E账号%' AND site = 'DE';

-- 示例：更新F账号店铺
-- UPDATE stores SET inventory_name = 'F-DE' WHERE name LIKE '%F账号%' AND site = 'DE';
-- UPDATE stores SET inventory_name = 'F-FR' WHERE name LIKE '%F账号%' AND site = 'FR';
-- UPDATE stores SET inventory_name = 'F-IT' WHERE name LIKE '%F账号%' AND site = 'IT';
-- UPDATE stores SET inventory_name = 'F-ES' WHERE name LIKE '%F账号%' AND site = 'ES';
-- UPDATE stores SET inventory_name = 'F-NL' WHERE name LIKE '%F账号%' AND site = 'NL';
-- UPDATE stores SET inventory_name = 'F-SE' WHERE name LIKE '%F账号%' AND site = 'SE';
-- UPDATE stores SET inventory_name = 'F-PL' WHERE name LIKE '%F账号%' AND site = 'PL';
-- UPDATE stores SET inventory_name = 'F-BE' WHERE name LIKE '%F账号%' AND site = 'BE';

-- 示例：更新G站点店铺
-- UPDATE stores SET inventory_name = 'G-USA' WHERE name LIKE '%G站点%' AND site = 'US';
-- UPDATE stores SET inventory_name = 'G-CA' WHERE name LIKE '%G站点%' AND site = 'CA';
-- UPDATE stores SET inventory_name = 'G-MX' WHERE name LIKE '%G站点%' AND site = 'MX';

-- 3. 创建视图方便查询
CREATE OR REPLACE VIEW store_inventory_mapping AS
SELECT
    s.id,
    s.name AS store_name,
    s.site,
    s.inventory_name,
    CONCAT(s.inventory_name, '-', s.site) AS inventory_account
FROM stores s
WHERE s.inventory_name IS NOT NULL;

-- 4. 查询验证
-- SELECT * FROM store_inventory_mapping;
