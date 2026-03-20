#!/bin/bash
# GitHub推送脚本

set -e  # 遇到错误时退出

echo "=== InfoData项目GitHub推送脚本 ==="
echo ""

# 检查Git状态
echo "1. 检查Git状态..."
git status

echo ""
read -p "是否继续推送到GitHub? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "操作已取消"
    exit 0
fi

echo ""
echo "2. 设置GitHub远程仓库..."
echo "请选择:"
echo "  a) 创建新GitHub仓库"
echo "  b) 推送到现有仓库"
echo "  c) 查看当前远程仓库"
read -p "选择 (a/b/c): " -n 1 -r
echo ""

case $REPLY in
    a|A)
        echo "请先在GitHub上创建新仓库:"
        echo "1. 访问 https://github.com/new"
        echo "2. 仓库名称: InfoData (或其他名称)"
        echo "3. 不要初始化README、.gitignore或license"
        echo ""
        read -p "请输入GitHub用户名: " github_user
        read -p "请输入仓库名称: " repo_name
        remote_url="https://github.com/${github_user}/${repo_name}.git"
        
        # 添加远程仓库
        git remote add origin $remote_url
        echo "✅ 远程仓库已添加: $remote_url"
        ;;
        
    b|B)
        read -p "请输入GitHub仓库URL (https://github.com/用户名/仓库名.git): " remote_url
        # 移除现有远程仓库
        git remote remove origin 2>/dev/null || true
        # 添加新远程仓库
        git remote add origin $remote_url
        echo "✅ 远程仓库已更新: $remote_url"
        ;;
        
    c|C)
        echo "当前远程仓库:"
        git remote -v
        exit 0
        ;;
        
    *)
        echo "无效选择，退出"
        exit 1
        ;;
esac

echo ""
echo "3. 重命名主分支为main..."
# 检查当前分支名
current_branch=$(git branch --show-current)
if [ "$current_branch" != "main" ]; then
    git branch -M main
    echo "✅ 分支重命名为: main"
else
    echo "✅ 当前已在main分支"
fi

echo ""
echo "4. 推送到GitHub..."
echo "推送内容:"
echo "  - 分支: main"
echo "  - 提交: $(git log --oneline -1)"
echo "  - 文件数: $(git diff --stat HEAD~1 HEAD 2>/dev/null | tail -1 || echo "初始提交")"
echo ""

read -p "确认推送? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "操作已取消"
    exit 0
fi

# 执行推送
echo "正在推送..."
if git push -u origin main; then
    echo ""
    echo "🎉 推送成功!"
    echo ""
    echo "仓库信息:"
    echo "  - URL: $remote_url"
    echo "  - 分支: main"
    echo "  - 最新提交: $(git log --oneline -1)"
    echo ""
    echo "下一步:"
    echo "1. 访问GitHub查看仓库: $(echo $remote_url | sed 's/\.git$//')"
    echo "2. 设置仓库描述和README"
    echo "3. 配置GitHub Actions自动化"
else
    echo ""
    echo "❌ 推送失败!"
    echo ""
    echo "可能的原因:"
    echo "1. 网络连接问题"
    echo "2. 认证失败 (需要GitHub Token)"
    echo "3. 权限不足"
    echo ""
    echo "解决方案:"
    echo "1. 检查网络连接"
    echo "2. 使用SSH密钥或Personal Access Token"
    echo "3. 确保有仓库写入权限"
    exit 1
fi

echo ""
echo "=== 推送完成 ==="