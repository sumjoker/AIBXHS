import React, { useState, useEffect } from 'react'
import { Card, Table, Button, Modal, Form, Input, Select, message, Popconfirm, Space, Tag } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, SearchOutlined } from '@ant-design/icons'
import { storesApi, departmentsApi } from '../api'
import { useTheme } from '../contexts/ThemeContext'

interface Store {
  id: number
  name: string
  platform: string
  site: string
  platform_store_id: string
  status: string
  department_id: number | null
  department_name: string
  inventory_name: string | null
  created_at: string
}

interface Department {
  id: number
  name: string
}

const StoreManagement: React.FC = () => {
  const { currentTheme } = useTheme()
  const [stores, setStores] = useState<Store[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingStore, setEditingStore] = useState<Store | null>(null)
  const [form] = Form.useForm()
  const [searchForm] = Form.useForm()
  const [filters, setFilters] = useState({ name_search: '', site_search: '' })
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [batchModalOpen, setBatchModalOpen] = useState(false)
  const [batchForm] = Form.useForm()

  const platformOptions = [
    { label: 'Amazon', value: 'amazon' },
    { label: 'Shopee', value: 'shopee' },
    { label: 'Lazada', value: 'lazada' },
    { label: 'TikTok', value: 'tiktok' },
    { label: 'Other', value: 'other' },
  ]

  const statusOptions = [
    { label: 'Active', value: 'active' },
    { label: 'Inactive', value: 'inactive' },
    { label: 'Error', value: 'error' },
  ]

  useEffect(() => {
    fetchData()
  }, [pagination.current, pagination.pageSize, filters])

  const fetchData = async () => {
    setLoading(true)
    try {
      const [storesRes, deptsRes] = await Promise.all([
        storesApi.getList({
          page: pagination.current,
          page_size: pagination.pageSize,
          ...filters,
        }),
        departmentsApi.getList(),
      ])
      if (storesRes.data.success) {
        setStores(storesRes.data.data)
        setPagination((prev) => ({ ...prev, total: storesRes.data.total }))
      }
      if (deptsRes.data.success) setDepartments(deptsRes.data.data)
    } catch (e) {
      console.error('获取数据失败:', e)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async () => {
    const values = await searchForm.validateFields()
    setFilters(values)
    setPagination((prev) => ({ ...prev, current: 1 }))
  }

  const handleReset = () => {
    searchForm.resetFields()
    setFilters({ name_search: '', site_search: '' })
    setPagination((prev) => ({ ...prev, current: 1 }))
  }

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys: React.Key[]) => {
      setSelectedRowKeys(newSelectedRowKeys)
    },
  }

  const handleBatchAssign = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要分配部门的店铺')
      return
    }
    batchForm.resetFields()
    setBatchModalOpen(true)
  }

  const handleBatchSubmit = async () => {
    try {
      const values = await batchForm.validateFields()
      await storesApi.batchUpdateDepartment({
        store_ids: selectedRowKeys as number[],
        department_id: values.department_id,
      })
      message.success('批量分配部门成功')
      setBatchModalOpen(false)
      setSelectedRowKeys([])
      fetchData()
    } catch (e: any) {
      if (e.errorFields) return
      message.error('批量分配失败')
    }
  }

  const handleCreate = () => {
    setEditingStore(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (store: Store) => {
    setEditingStore(store)
    form.setFieldsValue({
      name: store.name,
      platform: store.platform,
      site: store.site,
      platform_store_id: store.platform_store_id,
      department_id: store.department_id,
      status: store.status,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      if (editingStore) {
        await storesApi.update(editingStore.id, values)
        message.success('店铺更新成功')
      } else {
        await storesApi.create(values)
        message.success('店铺创建成功')
      }
      setModalOpen(false)
      fetchData()
    } catch (e: any) {
      if (e.errorFields) return
      message.error('操作失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await storesApi.delete(id)
      message.success('店铺删除成功')
      fetchData()
    } catch (e) {
      message.error('删除失败')
    }
  }

  const columns = [
    { title: '店铺名称', dataIndex: 'name', key: 'name' },
    { title: '店铺', dataIndex: 'inventory_name', key: 'inventory_name' },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      render: (platform: string) => (
        <Tag color="blue">{platform}</Tag>
      ),
    },
    { title: '站点', dataIndex: 'site', key: 'site' },
    { title: '店铺ID', dataIndex: 'platform_store_id', key: 'platform_store_id' },
    {
      title: '所属部门',
      dataIndex: 'department_name',
      key: 'department_name',
      render: (name: string) => (
        <Tag color="green">{name}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => {
        const colorMap: Record<string, string> = {
          active: 'success',
          inactive: 'default',
          error: 'error',
        }
        return <Tag color={colorMap[status] || 'default'}>{status}</Tag>
      },
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at' },
    {
      title: '操作',
      key: 'actions',
      render: (_: any, record: Store) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确定删除?" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, overflow: 'auto', height: '100%' }}>
      <Card
        loading={loading}
        title={
          <Form form={searchForm} layout="inline" style={{ margin: 0, width: '100%' }}>
            <Form.Item name="name_search" label="店铺名">
              <Input placeholder="请输入店铺名" style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="site_search" label="站点">
              <Input placeholder="请输入站点" style={{ width: 150 }} />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
                  搜索
                </Button>
                <Button onClick={handleReset}>重置</Button>
              </Space>
            </Form.Item>
          </Form>
        }
        extra={
          <Space>
            {selectedRowKeys.length > 0 && (
              <Button type="default" onClick={handleBatchAssign}>
                批量分配部门 ({selectedRowKeys.length})
              </Button>
            )}
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新增店铺
            </Button>
          </Space>
        }
      >
        <Table
          dataSource={stores}
          columns={columns}
          rowKey="id"
          rowSelection={rowSelection}
          pagination={{
            current: pagination.current,
            pageSize: pagination.pageSize,
            total: pagination.total,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条`,
            pageSizeOptions: ['10', '20', '50', '100'],
            onChange: (page, pageSize) =>
              setPagination((prev) => ({ ...prev, current: page, pageSize: pageSize || 20 })),
          }}
        />
      </Card>

      <Modal
        title={editingStore ? '编辑店铺' : '新增店铺'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="店铺名称"
            rules={[{ required: true, message: '请输入店铺名称' }]}
          >
            <Input placeholder="请输入店铺名称" />
          </Form.Item>
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: '请选择平台' }]}
            initialValue="amazon"
          >
            <Select placeholder="请选择平台" options={platformOptions} />
          </Form.Item>
          <Form.Item name="site" label="站点">
            <Input placeholder="请输入站点，如US、UK等" />
          </Form.Item>
          <Form.Item name="platform_store_id" label="平台店铺ID">
            <Input placeholder="请输入平台店铺ID" />
          </Form.Item>
          <Form.Item name="department_id" label="所属部门">
            <Select
              placeholder="请选择部门"
              options={departments.map((d) => ({ label: d.name, value: d.id }))}
              allowClear
            />
          </Form.Item>
          {editingStore && (
            <Form.Item name="status" label="状态">
              <Select placeholder="请选择状态" options={statusOptions} />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal
        title="批量分配部门"
        open={batchModalOpen}
        onOk={handleBatchSubmit}
        onCancel={() => setBatchModalOpen(false)}
      >
        <Form form={batchForm} layout="vertical">
          <Form.Item
            name="department_id"
            label="选择部门"
          >
            <Select placeholder="请选择部门（不选择则取消分配）" allowClear>
              {departments.map((dept) => (
                <Select.Option key={dept.id} value={dept.id}>
                  {dept.name}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <div style={{ color: '#999', fontSize: '12px' }}>
            已选择 {selectedRowKeys.length} 个店铺进行批量分配
          </div>
        </Form>
      </Modal>
    </div>
  )
}

export default StoreManagement
