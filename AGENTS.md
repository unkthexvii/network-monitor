# Network Monitor Project

## Codebase Memory (codebase-memory-mcp)

This project has codebase-memory-mcp configured for code intelligence.

### Starting the Server

When you need to analyze code structure or when the user asks about code relationships:

1. **Check if server is running:**
   ```powershell
   netstat -ano | findstr :9749
   ```

2. **If not running, start it:**
   ```powershell
   C:\Users\unk\Desktop\network-monitor\start-codebase-memory.bat
   ```

3. **Or start directly:**
   ```powershell
   Start-Process -FilePath "C:\Users\unk\Desktop\network-monitor\codebase-memory-mcp.exe" -ArgumentList "--ui=true", "--port=9749" -WorkingDirectory "C:\Users\unk\Desktop\network-monitor"
   ```

4. **Verify it's running:**
   ```powershell
   netstat -ano | findstr :9749
   ```

### Available Tools

Once running, use these MCP tools:
- `index_repository(repo_path="C:/Users/unk/Desktop/network-monitor")` - Index the project
- `search_graph(project="network-monitor", name_pattern=".*monitor.*")` - Find functions
- `trace_path(project="network-monitor", function_name="start_monitoring")` - Trace calls
- `get_architecture(project="network-monitor")` - Project overview
- `detect_changes(project="network-monitor")` - Impact analysis

### UI Access

Graph visualization: http://localhost:9749

### Stopping the Server

```powershell
taskkill /F /IM codebase-memory-mcp.exe
```

Or use: `C:\Users\unk\Desktop\network-monitor\stop-codebase-memory.bat`