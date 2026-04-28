package io.vectorize.hindsight.android

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
            "tar", "xf", archivePath, "-C", baseDir,
            env = emptyMap()
        )
        Log.i(TAG, "Extract: ${extractResult.take(200)}")

        // Set up LD_LIBRARY_PATH to include both native dir and extracted libs
        val ldPath = "$nativeDir:$baseDir/lib"

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
        val setupScript = File("$pgBinDir/setup.sh")
        setupScript.writeText("""#!/system/bin/sh
export LD_LIBRARY_PATH=$ldPath
export TMPDIR=$tmpDir
$pgBinDir/initdb -D $dataDir -L $baseDir/share/postgresql --auth=trust --username=hindsight --no-locale --no-clean 2>&1 || true
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

        // PG has the Termux share path hardcoded. Try to create it, or binary-patch.
        // On test devices, /data/data/com.termux doesn't exist, so we can create it.
        val termuxShareDir = "/data/data/com.termux/files/usr/share/postgresql"
        val termuxLibDir = "/data/data/com.termux/files/usr/lib"
        exec("/system/bin/sh", "-c",
            "mkdir -p $termuxShareDir && cp -r $baseDir/share/postgresql/* $termuxShareDir/ && " +
            "mkdir -p $termuxLibDir && cp $baseDir/lib/*.so* $termuxLibDir/ 2>/dev/null; " +
            "mkdir -p $termuxLibDir/postgresql && cp $baseDir/lib/postgresql/*.so* $termuxLibDir/postgresql/ 2>/dev/null; " +
            "echo TERMUX_DIR_CREATED",
            env = emptyMap()
        )
        Log.i(TAG, "Termux share dir: ${File("$termuxShareDir/postgres.bki").exists()}")

        // Create share dir relative to nativeLibDir so PG's path resolution finds it
        // PG resolves: <binary_dir>/../share/postgresql/
        // nativeLibDir is like /data/app/.../lib/arm64
        // So we need:       /data/app/.../lib/share/postgresql/ (one level up from arm64)
        val nativeParent = File(nativeDir).parent ?: nativeDir
        val nativeShareDir = "$nativeParent/share/postgresql"
        File(nativeShareDir).mkdirs()
        // Symlink our extracted share data there
        exec("/system/bin/sh", "-c",
            "ln -sf $baseDir/share/postgresql/* $nativeShareDir/ 2>&1 || cp -r $baseDir/share/postgresql/* $nativeShareDir/",
            env = emptyMap()
        )
        Log.i(TAG, "Share dir linked at: $nativeShareDir (exists: ${File("$nativeShareDir/postgres.bki").exists()})")

        // Also try creating at the pg-bin level: pg-bin/../share/postgresql
        val pgBinShareDir = "$baseDir/share/postgresql"
        Log.i(TAG, "Share dir at pgBin/../share: $pgBinShareDir (exists: ${File("$pgBinShareDir/postgres.bki").exists()})")

        // 4c: Run postgres --boot to execute bootstrap SQL
        val bootScript = File("$pgBinDir/boot.sh")
        bootScript.writeText("""#!/system/bin/sh
export LD_LIBRARY_PATH=$ldPath
export TMPDIR=$tmpDir

# Run bootstrap with postgres --boot
# This reads postgres.bki from stdin and creates the system catalog
$pgBinDir/postgres --boot -X 1048576 -F -c dynamic_shared_memory_type=mmap -D $dataDir < $baseDir/share/postgresql/postgres.bki 2>&1
BOOT_EXIT=${'$'}?
echo "BOOT_EXIT=${'$'}BOOT_EXIT"

if [ ${'$'}BOOT_EXIT -eq 0 ]; then
    # Run the additional setup SQL files that initdb normally runs
    for sql in system_constraints.sql system_functions.sql system_views.sql information_schema.sql; do
        if [ -f "$baseDir/share/postgresql/${'$'}sql" ]; then
            echo "Running ${'$'}sql..."
            $pgBinDir/postgres --single -D $dataDir -c dynamic_shared_memory_type=mmap template1 < "$baseDir/share/postgresql/${'$'}sql" 2>&1 || true
        fi
    done

    # Create the default databases
    $pgBinDir/postgres --single -D $dataDir -c dynamic_shared_memory_type=mmap template1 2>&1 <<EOSQL
CREATE DATABASE postgres;
CREATE DATABASE hindsight;
EOSQL
    echo "DATABASES_CREATED"
fi
""")
        bootScript.setExecutable(true, false)

        val bootResult = exec("/system/bin/sh", bootScript.absolutePath, env = pgEnvMap, timeoutMs = 300_000)
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
            "$pgBinDir/postgres",
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
                "$pgBinDir/pg_isready", "-h", tmpDir, "-p", "15432",
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
            exec("$pgBinDir/createdb", "-h", tmpDir, "-p", "15432", "-U", "hindsight", "hindsight", env = pgEnv)

            // Step 7: Test pgvector
            Log.i(TAG, "Step 7: Testing pgvector...")
            val vectorResult = exec(
                "$pgBinDir/psql", "-h", tmpDir, "-p", "15432", "-U", "hindsight", "-d", "hindsight",
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
