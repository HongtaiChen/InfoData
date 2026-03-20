#!/bin/bash
echo "=== GitHub推送脚本 ==="
echo "GitHub用户名: ou-dc1f4439c98275896e6b0b9331e20da0"
echo ""
echo "步骤1: 确保GitHub仓库已创建"
echo "请先在GitHub创建仓库: https://github.com/new"
echo "仓库名: InfoData, 公开仓库, 不要初始化文件"
echo ""
read -p "仓库已创建? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "请先创建GitHub仓库"
    exit 1
fi
echo ""
echo "步骤2: 推送到GitHub..."
git remote remove origin 2>/dev/null || true
git remote add origin git@github.com:ou-dc1f4439c98275896e6b0b9331e20da0/InfoData.git
if git push -u origin main; then
    echo ""
    echo "🎉 推送成功!"
    echo "仓库地址: https://github.com/ou-dc1f4439c98275896e6b0b9331e20da0/InfoData"
    echo "提交信息: $(git log --oneline -1)"
else
    echo ""
    echo "❌ 推送失败!"
    echo "可能原因:"
    echo "1. 仓库不存在"
    echo "2. 权限问题"
    echo "3. 网络连接"
    echo ""
    echo "解决方案:"
    echo "1. 确认仓库已创建: https://github.com/ou-dc1f4439c98275896e6b0b9331e20da0/InfoData"
    echo "2. 检查SSH密钥: ssh -T git@github.com"
fi
