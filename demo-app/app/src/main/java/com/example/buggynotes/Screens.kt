package com.example.buggynotes

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/**
 * Seeded bugs. Start the app with `--es bug <name>` to switch one on:
 * save_lost, edit_not_saved, wrong_search, freeze_search, crash_settings, wrong_version,
 * missing_delete, crash_on_delete, count_wrong, invisible_titles.
 */
class MainActivity : StoreActivity() {
    private var filter = ""
    private lateinit var column: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent.getStringExtra("bug")?.let { store.bug = it }
        column = page()
    }

    override fun onResume() {
        super.onResume()
        render()
    }

    private fun render() {
        column.removeAllViews()
        val all = store.titles
        val shown = when {
            filter.isEmpty() || store.bug == "wrong_search" -> all
            else -> all.filter { it.contains(filter, ignoreCase = true) }
        }

        val count = if (store.bug == "count_wrong") all.size + 1 else all.size
        column.addView(TextView(this).apply {
            text = if (store.showCount) "My notes: $count" else "My notes"
            textSize = 28f
        })
        val search = EditText(this).apply {
            hint = "Search text"
            setText(filter)
        }
        column.addView(search)
        column.addView(Button(this).apply {
            text = "Search"
            setOnClickListener {
                if (store.bug == "freeze_search") Thread.sleep(12_000)
                filter = search.text.toString()
                render()
            }
        })
        column.addView(Button(this).apply {
            text = "New note"
            setOnClickListener { startActivity(Intent(this@MainActivity, EditActivity::class.java)) }
        })
        column.addView(Button(this).apply {
            text = "Settings"
            setOnClickListener { startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) }
        })
        for (title in shown) {
            column.addView(TextView(this).apply {
                text = title
                textSize = 22f
                setPadding(0, 36, 0, 36)
                // Visible only in a screenshot: the text is still present in the UI tree.
                if (store.bug == "invisible_titles") setTextColor(Color.parseColor("#FAFAFA"))
                setOnClickListener {
                    startActivity(Intent(this@MainActivity, EditActivity::class.java).putExtra("title", title))
                }
            })
        }
    }
}

class EditActivity : StoreActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val original = intent.getStringExtra("title")
        val column = page()
        column.addView(TextView(this).apply {
            text = if (original == null) "New note" else "Edit note"
            textSize = 28f
        })
        val titleBox = EditText(this).apply {
            hint = "Title"
            setText(original ?: "")
        }
        column.addView(titleBox)
        column.addView(Button(this).apply {
            text = "Save"
            setOnClickListener {
                save(original, titleBox.text.toString())
                finish()
            }
        })
        column.addView(Button(this).apply {
            text = "Delete"
            visibility = if (original == null || store.bug == "missing_delete") View.GONE else View.VISIBLE
            setOnClickListener {
                check(store.bug != "crash_on_delete") { "Seeded bug: crash_on_delete" }
                store.titles = store.titles - original!!
                finish()
            }
        })
    }

    private fun save(original: String?, title: String) {
        if (original == null) {
            if (store.bug != "save_lost") store.titles = store.titles + title
        } else if (store.bug != "edit_not_saved") {
            store.titles = store.titles.map { if (it == original) title else it }
        }
    }
}

class SettingsActivity : StoreActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        check(store.bug != "crash_settings") { "Seeded bug: crash_settings" }
        val column = page()
        column.addView(TextView(this).apply {
            text = "Settings"
            textSize = 28f
        })
        column.addView(Switch(this).apply {
            text = "Show note count"
            isChecked = store.showCount
            setOnCheckedChangeListener { _, on -> store.showCount = on }
        })
        column.addView(TextView(this).apply {
            text = if (store.bug == "wrong_version") "Version 0.9" else "Version 1.0"
            setPadding(0, 36, 0, 0)
        })
        column.addView(Button(this).apply {
            text = "Back to notes"
            setOnClickListener { finish() }
        })
    }
}
