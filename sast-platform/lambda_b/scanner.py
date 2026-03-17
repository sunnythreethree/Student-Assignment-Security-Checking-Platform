"""
Scanning Engine Module
Responsible for integrating Bandit (Python code scanning) and Semgrep (Java/JS code scanning)
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SecurityScanner:
    """Security scanner class, integrating Bandit and Semgrep"""
    
    def __init__(self):
        self.temp_dir = None
    
    def scan_code(self, code: str, language: str, scan_id: str) -> Dict[str, Any]:
        """
        Choose appropriate scanner based on language type to scan code
        
        Args:
            code: Code content to be scanned
            language: Code language (python, java, javascript)
            scan_id: Scan task ID
            
        Returns:
            Dictionary containing scan results
        """
        try:
            # Create temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                self.temp_dir = temp_dir
                
                # Choose scanner based on language
                if language.lower() == 'python':
                    return self._scan_with_bandit(code, scan_id)
                elif language.lower() in ['java', 'javascript', 'js']:
                    return self._scan_with_semgrep(code, language, scan_id)
                else:
                    raise ValueError(f"Unsupported language type: {language}")
                    
        except Exception as e:
            logger.error(f"Scan failed - scan_id: {scan_id}, error: {str(e)}")
            return {
                'scan_id': scan_id,
                'language': language,
                'tool': 'error',
                'error': str(e),
                'findings': [],
                'summary': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
            }
    
    def _scan_with_bandit(self, code: str, scan_id: str) -> Dict[str, Any]:
        """
        Use Bandit to scan Python code
        
        Args:
            code: Python code content
            scan_id: Scan task ID
            
        Returns:
            Bandit scan results
        """
        logger.info(f"Starting Bandit scan - scan_id: {scan_id}")
        
        # Write temporary Python file
        python_file = os.path.join(self.temp_dir, f"code_{scan_id}.py")
        with open(python_file, 'w', encoding='utf-8') as f:
            f.write(code)
        
        try:
            # Run Bandit scan
            cmd = [
                'bandit',
                '-r', python_file,
                '-f', 'json',
                '--silent'  # Reduce output noise
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                cwd=self.temp_dir
            )
            
            # Bandit return codes: 0=no issues, 1=has issues but successful, >=2=error
            if result.returncode >= 2:
                raise RuntimeError(f"Bandit execution failed: {result.stderr}")
            
            # Parse JSON results
            if result.stdout.strip():
                bandit_output = json.loads(result.stdout)
            else:
                # No issues found
                bandit_output = {
                    "results": [],
                    "metrics": {
                        "CONFIDENCE.HIGH": 0,
                        "CONFIDENCE.MEDIUM": 0,
                        "CONFIDENCE.LOW": 0,
                        "SEVERITY.HIGH": 0,
                        "SEVERITY.MEDIUM": 0,
                        "SEVERITY.LOW": 0
                    }
                }
            
            logger.info(f"Bandit scan completed - scan_id: {scan_id}, issues found: {len(bandit_output.get('results', []))}")
            
            return {
                'scan_id': scan_id,
                'language': 'python',
                'tool': 'bandit',
                'raw_output': bandit_output,
                'findings': bandit_output.get('results', []),
                'metrics': bandit_output.get('metrics', {})
            }
            
        except subprocess.TimeoutExpired:
            raise RuntimeError("Bandit scan timeout")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Bandit output parsing failed: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Bandit scan exception: {str(e)}")
    
    def _scan_with_semgrep(self, code: str, language: str, scan_id: str) -> Dict[str, Any]:
        """
        Use Semgrep to scan Java/JavaScript code
        
        Args:
            code: Code content
            language: Language type
            scan_id: Scan task ID
            
        Returns:
            Semgrep scan results
        """
        logger.info(f"Starting Semgrep scan - scan_id: {scan_id}, language: {language}")
        
        # Determine file extension based on language
        ext_map = {
            'java': '.java',
            'javascript': '.js',
            'js': '.js'
        }
        
        file_ext = ext_map.get(language.lower(), '.txt')
        code_file = os.path.join(self.temp_dir, f"code_{scan_id}{file_ext}")
        
        # 写入临时代码文件
        with open(code_file, 'w', encoding='utf-8') as f:
            f.write(code)
        
        try:
            # 运行 Semgrep 扫描
            cmd = [
                'semgrep',
                '--config=auto',  # 使用自动规则集
                '--json',         # JSON 输出
                '--quiet',        # 减少输出
                '--no-git-ignore', # 忽略 .gitignore
                code_file
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5分钟超时
                cwd=self.temp_dir
            )
            
            # Semgrep 返回码: 0=无问题, 1=有问题, >=2=错误
            if result.returncode >= 2:
                raise RuntimeError(f"Semgrep 执行失败: {result.stderr}")
            
            # 解析 JSON 结果
            if result.stdout.strip():
                semgrep_output = json.loads(result.stdout)
            else:
                semgrep_output = {"results": []}
            
            results = semgrep_output.get('results', [])
            logger.info(f"Semgrep 扫描完成 - scan_id: {scan_id}, 发现问题: {len(results)}")
            
            return {
                'scan_id': scan_id,
                'language': language,
                'tool': 'semgrep',
                'raw_output': semgrep_output,
                'findings': results
            }
            
        except subprocess.TimeoutExpired:
            raise RuntimeError("Semgrep 扫描超时")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Semgrep 输出解析失败: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Semgrep 扫描异常: {str(e)}")


def scan_code_with_timeout(code: str, language: str, scan_id: str, timeout: int = 300) -> Dict[str, Any]:
    """
    带超时的代码扫描函数
    
    Args:
        code: 要扫描的代码
        language: 代码语言
        scan_id: 扫描ID
        timeout: 超时时间（秒）
        
    Returns:
        扫描结果
    """
    scanner = SecurityScanner()
    return scanner.scan_code(code, language, scan_id)