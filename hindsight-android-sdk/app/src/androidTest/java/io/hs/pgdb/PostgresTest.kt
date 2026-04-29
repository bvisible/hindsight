package io.hs.pgdb

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.FileOutputStream

/**
 * Instrumented test: proves PostgreSQL + pgvector runs on Android ARM64.
 *
 * PG binaries are bundled as .so files in jniLibs (for SELinux exec permission)
 * and shared libraries/data files are in assets.
 */
@RunWith(AndroidJUnit4::class)
class PostgresTest {

    companion object {
        private const val TAG = "PostgresTest"
    }

    @Test
    fun testPostgresWithPgvector() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val baseDir = context.filesDir.absolutePath
        val nativeDir = context.applicationInfo.nativeLibraryDir
        val dataDir = "$baseDir/pg-data"
        val logFile = "$baseDir/pg.log"
        val tmpDir = "$baseDir/tmp"

        Log.i(TAG, "Native lib dir: $nativeDir")
        Log.i(TAG, "Files dir: $baseDir")

        // Step 1: Find PG binaries (installed as .so in native lib dir)
        val postgresBin = "$nativeDir/libpostgres.so"
        val initdbBin = "$nativeDir/libinitdb.so"
        val createdbBin = "$nativeDir/libcreatedb.so"
        val psqlBin = "$nativeDir/libpsql.so"
        val pgIsReadyBin = "$nativeDir/libpg_isready.so"
        val pgCtlBin = "$nativeDir/libpg_ctl.so"

        assertTrue("postgres binary not found at $postgresBin", File(postgresBin).exists())
        Log.i(TAG, "Step 1: PG binaries found in native lib dir")

        // Copy binaries with correct names so initdb can find postgres in same dir
        val pgBinDir = "$baseDir/pg-bin"
        File(pgBinDir).deleteRecursively()
        File(pgBinDir).mkdirs()
        mapOf(
            "postgres" to "libpostgres.so",
            "initdb" to "libinitdb.so",
            "createdb" to "libcreatedb.so",
            "psql" to "libpsql.so",
            "pg_isready" to "libpg_isready.so",
            "pg_ctl" to "libpg_ctl.so"
        ).forEach { (name, soName) ->
            val source = File("$nativeDir/$soName")
            val dest = File("$pgBinDir/$name")
            if (source.exists()) {
                source.inputStream().use { inp ->
                    FileOutputStream(dest).use { out -> inp.copyTo(out) }
                }
                dest.setExecutable(true, false)
                Runtime.getRuntime().exec(arrayOf("chmod", "755", dest.absolutePath)).waitFor()
                Log.i(TAG, "  Copied $soName -> $name (${dest.length()} bytes, exec=${dest.canExecute()})")
            } else {
                Log.w(TAG, "  Missing: $soName in $nativeDir")
            }
        }

        // Step 2: Extract shared libs from assets to a writable location
        Log.i(TAG, "Step 2: Extracting shared libraries from assets...")
        val pgLibDir = "$baseDir/pg-lib"
        val pgShareDir = "$baseDir/pg-share"
        File(pgLibDir).mkdirs()
        File(pgShareDir).mkdirs()
        File(tmpDir).mkdirs()

        // Extract the tar from assets (contains lib/ and share/)
        val archivePath = "$baseDir/postgres-arm64.tar"
        context.assets.open("postgres-arm64.tar").use { input ->
            FileOutputStream(archivePath).use { output ->
                input.copyTo(output)
            }
        }

        val extractResult = exec(
            "/system/bin/sh", "-c", "cd $baseDir && tar xf $archivePath 2>&1 || true",
            env = emptyMap()
        )
        Log.i(TAG, "Extract: ${extractResult.take(200)}")

        // Set up LD_LIBRARY_PATH to include native dir, extracted libs, and patched prefix
        val pgPrefix = "$baseDir/usr"  // matches binary: /data/data/io.hs.pgdb/files/usr
        val ldPath = "$nativeDir:$baseDir/lib:$pgPrefix/lib"

        // Step 3: Check postgres version
        // Try pg-bin copy first, fall back to nativeDir .so name
        val versionResult = try {
            exec("$pgBinDir/postgres", "--version", env = mapOf("LD_LIBRARY_PATH" to ldPath))
        } catch (e: Exception) {
            Log.w(TAG, "pg-bin exec failed (${e.message}), trying nativeDir directly")
            exec("$nativeDir/libpostgres.so", "--version", env = mapOf("LD_LIBRARY_PATH" to ldPath))
        }
        Log.i(TAG, "Step 3: $versionResult")
        assertTrue("postgres --version failed: $versionResult", versionResult.contains("PostgreSQL"))

        // Step 4: Manual bootstrap (skips initdb's broken probe)
        //
        // initdb's "selecting default max_connections" probe spawns a postgres
        // subprocess that fails on Android virtual devices. Instead, we:
        // 1. Let initdb create dirs + configs (--no-clean keeps files on failure)
        // 2. Write our own postgresql.conf with known-good settings
        // 3. Run "postgres --boot" to execute the bootstrap SQL (postgres.bki)
        Log.i(TAG, "Step 4: Manual bootstrap...")
        File(dataDir).deleteRecursively()
        File(dataDir).mkdirs()

        val pgEnvMap = mapOf(
            "LD_LIBRARY_PATH" to ldPath,
            "TMPDIR" to tmpDir,
            "PGDATA" to dataDir,
            "PGSHAREDIR" to "$baseDir/share/postgresql"
        )

        // 4a: Let initdb create directory structure (it will fail at probe, that's ok)
        // On physical devices, only nativeLibDir binaries are executable (SELinux).
        // Create wrapper scripts that call the .so binaries by their full path.
        val setupScript = File("$pgBinDir/setup.sh")
        setupScript.writeText("""#!/system/bin/sh
export LD_LIBRARY_PATH=$ldPath
export TMPDIR=$tmpDir
# Create wrapper scripts so initdb can find "postgres" when it forks
mkdir -p $pgBinDir/wrappers
for cmd in postgres initdb createdb psql pg_isready pg_ctl; do
    echo "#!/system/bin/sh" > $pgBinDir/wrappers/${'$'}cmd
    echo "exec $nativeDir/lib${'$'}{cmd}.so \"\${'$'}@\"" >> $pgBinDir/wrappers/${'$'}cmd
    chmod 755 $pgBinDir/wrappers/${'$'}cmd
done
export PATH=$pgBinDir/wrappers:${'$'}PATH
# initdb needs "postgres" binary in its dir. Create wrappers with correct names.
$nativeDir/libinitdb.so -D $dataDir -L $baseDir/share/postgresql --auth=trust --username=hindsight --no-locale --no-clean 2>&1 || true
echo "SETUP_DONE"
""")
        setupScript.setExecutable(true, false)
        val setupResult = exec("/system/bin/sh", setupScript.absolutePath, env = pgEnvMap, timeoutMs = 30_000)
        Log.i(TAG, "Setup result: ${setupResult.takeLast(200)}")

        // Verify directory structure was created
        assertTrue("PG_VERSION not created", File("$dataDir/PG_VERSION").exists())
        Log.i(TAG, "Directory structure created")

        // 4b: Write our own postgresql.conf with Android-friendly settings
        // NOTE: no timezone setting - PG can't find timezone data at hardcoded Termux path
        File("$dataDir/postgresql.conf").writeText("""
max_connections = 10
shared_buffers = 16MB
dynamic_shared_memory_type = mmap
work_mem = 4MB
maintenance_work_mem = 16MB
max_wal_size = 32MB
log_destination = 'stderr'
datestyle = 'iso, mdy'
lc_messages = 'C'
lc_monetary = 'C'
lc_numeric = 'C'
lc_time = 'C'
default_text_search_config = 'pg_catalog.english'
listen_addresses = ''
unix_socket_directories = '$tmpDir'
""".trimIndent())

        // PG binaries are patched to look for share/lib at /data/local/tmp/pgsql_hs_usr__/
        // Create that directory and symlink our extracted data there
        val prefixResult = exec("/system/bin/sh", "-c",
            "mkdir -p $pgPrefix/share/postgresql 2>&1 && " +
            "cp -r $baseDir/share/postgresql/* $pgPrefix/share/postgresql/ 2>&1 && " +
            "mkdir -p $pgPrefix/lib 2>&1 && " +
            "cp $baseDir/lib/*.so* $pgPrefix/lib/ 2>&1 && " +
            "ls $pgPrefix/share/postgresql/postgres.bki 2>&1 && " +
            "echo PREFIX_OK",
            env = emptyMap()
        )
        Log.i(TAG, "Prefix setup: ${prefixResult.takeLast(300)}")
        Log.i(TAG, "PG prefix at $pgPrefix: share=${File("$pgPrefix/share/postgresql/postgres.bki").exists()}")

        // 4c: Run postgres --boot to execute bootstrap SQL
        val bootScript = File("$pgBinDir/boot.sh")
        bootScript.writeText("""#!/system/bin/sh
export LD_LIBRARY_PATH=$ldPath
export TMPDIR=$tmpDir

# Run bootstrap with postgres --boot
# This reads postgres.bki from stdin and creates the system catalog
export PATH=$pgBinDir/wrappers:${'$'}PATH
# Log first and last lines of bki to verify file is readable
echo "BKI_LINES=$(wc -l < $baseDir/share/postgresql/postgres.bki)"
head -3 $baseDir/share/postgresql/postgres.bki

# Run boot with timeout and capture output to file
$nativeDir/libpostgres.so --boot -X 1048576 -F -c dynamic_shared_memory_type=mmap -D $dataDir < $baseDir/share/postgresql/postgres.bki > $baseDir/boot_out.log 2>&1 &
BOOT_PID=${'$'}!
echo "BOOT_PID=${'$'}BOOT_PID"

# Monitor for 120 seconds, checking progress every 10s
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 10
    if ! kill -0 ${'$'}BOOT_PID 2>/dev/null; then
        wait ${'$'}BOOT_PID
        BOOT_EXIT=${'$'}?
        echo "BOOT_COMPLETED after ${'$'}((i*10))s exit=${'$'}BOOT_EXIT"
        break
    fi
    LINES=$(wc -l < $baseDir/boot_out.log 2>/dev/null || echo 0)
    SIZE=$(wc -c < $baseDir/boot_out.log 2>/dev/null || echo 0)
    echo "BOOT_PROGRESS ${i}0s: ${LINES} lines, ${SIZE} bytes"
    tail -1 $baseDir/boot_out.log 2>/dev/null
done

# If still running after 120s, show what we have and kill
if kill -0 ${'$'}BOOT_PID 2>/dev/null; then
    echo "BOOT_STILL_RUNNING after 120s"
    echo "Last 5 lines:"
    tail -5 $baseDir/boot_out.log 2>/dev/null
    kill ${'$'}BOOT_PID 2>/dev/null
    BOOT_EXIT=1
else
    BOOT_EXIT=${'$'}?
fi

# Show global dir contents
ls -la $dataDir/global/ 2>/dev/null | head -10
echo "BOOT_EXIT=${'$'}BOOT_EXIT"

if [ ${'$'}BOOT_EXIT -eq 0 ]; then
    # Run the additional setup SQL files that initdb normally runs
    for sql in system_constraints.sql system_functions.sql system_views.sql information_schema.sql; do
        if [ -f "$baseDir/share/postgresql/${'$'}sql" ]; then
            echo "Running ${'$'}sql..."
            $nativeDir/libpostgres.so --single -D $dataDir -c dynamic_shared_memory_type=mmap template1 < "$baseDir/share/postgresql/${'$'}sql" 2>&1 || true
        fi
    done

    # Create the default databases
    $nativeDir/libpostgres.so --single -D $dataDir -c dynamic_shared_memory_type=mmap template1 2>&1 <<EOSQL
CREATE DATABASE postgres;
CREATE DATABASE hindsight;
EOSQL
    echo "DATABASES_CREATED"
fi
""")
        bootScript.setExecutable(true, false)

        val bootResult = exec("/system/bin/sh", bootScript.absolutePath, env = pgEnvMap, timeoutMs = 180_000)
        Log.i(TAG, "Boot result (${bootResult.length} chars): ${bootResult.takeLast(500)}")

        val pgControlExists = File("$dataDir/global/pg_control").exists()
        Log.i(TAG, "pg_control exists: $pgControlExists")

        if (!pgControlExists) {
            val globalFiles = File("$dataDir/global").listFiles()?.map { "${it.name} (${it.length()})" } ?: emptyList()
            Log.i(TAG, "global/ contents: $globalFiles")
        }
        assertTrue("Bootstrap failed - pg_control missing. Last output: ${bootResult.takeLast(300)}", pgControlExists)

        // Step 5: Start PostgreSQL
        Log.i(TAG, "Step 5: Starting PostgreSQL...")
        val pgEnv = mapOf(
            "LD_LIBRARY_PATH" to ldPath,
            "TMPDIR" to tmpDir,
            "PGSHAREDIR" to "$baseDir/share/postgresql"
        )

        // Start postgres directly (pg_ctl may not find the binary)
        val pgProcess = ProcessBuilder(
            "$nativeDir/libpostgres.so",
            "-D", dataDir,
            "-k", tmpDir,
            "-p", "15432",
            "-c", "listen_addresses="
        ).apply {
            environment().putAll(pgEnv)
            redirectErrorStream(true)
            redirectOutput(File(logFile))
        }.start()

        // Wait for PG to be ready
        var pgReady = false
        for (i in 1..30) {
            Thread.sleep(1000)
            val isReady = exec(
                "$nativeDir/libpg_isready.so", "-h", tmpDir, "-p", "15432",
                env = pgEnv
            )
            if (isReady.contains("accepting connections")) {
                pgReady = true
                Log.i(TAG, "PostgreSQL ready after ${i}s")
                break
            }
        }

        if (!pgReady) {
            val pgLog = try { File(logFile).readText() } catch (_: Exception) { "no log" }
            Log.e(TAG, "PG log: $pgLog")
        }
        assertTrue("PostgreSQL failed to start", pgReady)

        try {
            // Step 6: Create database
            Log.i(TAG, "Step 6: Creating database...")
            exec("$nativeDir/libcreatedb.so", "-h", tmpDir, "-p", "15432", "-U", "hindsight", "hindsight", env = pgEnv)

            // Step 7: Test pgvector
            Log.i(TAG, "Step 7: Testing pgvector...")
            val vectorResult = exec(
                "$nativeDir/libpsql.so", "-h", tmpDir, "-p", "15432", "-U", "hindsight", "-d", "hindsight",
                "-c", "CREATE EXTENSION IF NOT EXISTS vector; SELECT '[1,2,3]'::vector <-> '[4,5,6]'::vector AS distance;",
                env = pgEnv
            )
            Log.i(TAG, "pgvector result: $vectorResult")
            assertTrue("pgvector failed: $vectorResult", vectorResult.contains("5.196"))

            Log.i(TAG, "=== SUCCESS: PostgreSQL 18 + pgvector works on Android ARM64! ===")
        } finally {
            pgProcess.destroy()
            pgProcess.waitFor()
            Log.i(TAG, "PostgreSQL stopped")
        }
    }

    private fun exec(
        vararg command: String,
        env: Map<String, String>,
        timeoutMs: Long = 60_000
    ): String {
        val pb = ProcessBuilder(*command)
        pb.redirectErrorStream(true)
        pb.environment().putAll(env)

        env["TMPDIR"]?.let { File(it).mkdirs() }

        val process = try {
            pb.start()
        } catch (e: java.io.IOException) {
            Log.e(TAG, "Failed to start: ${command.joinToString(" ")}: ${e.message}")
            throw e
        }

        // Read output in a separate thread to avoid blocking
        val output = StringBuilder()
        val readerThread = Thread {
            process.inputStream.bufferedReader().forEachLine { line ->
                output.appendLine(line)
            }
        }
        readerThread.isDaemon = true
        readerThread.start()

        val completed = process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
        if (!completed) {
            Log.w(TAG, "Command timed out after ${timeoutMs}ms: ${command.joinToString(" ")}")
            process.destroyForcibly()
        } else {
            Log.i(TAG, "Command exited with code ${process.exitValue()}: ${command.first().substringAfterLast('/')}")
        }
        readerThread.join(2000)

        return output.toString()
    }
}
