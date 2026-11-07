# Segment 2 test fixture: a native WinForms form with real UIA metadata.
# Run: powershell -STA -ExecutionPolicy Bypass -File scripts\test_form.ps1
# The window title is "Excel Agent Test Form" (see config/agent.test.json).

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "Excel Agent Test Form"
$form.ClientSize = New-Object System.Drawing.Size(520, 430)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false

function Add-Field {
    param(
        [string]$Caption,
        [string]$Name,
        [int]$Top,
        [string]$AccessibleName
    )

    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Caption
    $label.Left = 20
    $label.Top = $Top + 3
    $label.Width = 120
    $label.AccessibleName = "$AccessibleName label"
    [void]$form.Controls.Add($label)

    $box = New-Object System.Windows.Forms.TextBox
    $box.Name = $Name
    $box.Left = 150
    $box.Top = $Top
    $box.Width = 340
    $box.AccessibleName = $AccessibleName
    [void]$form.Controls.Add($box)
    return $box
}

$invoice = Add-Field -Caption "Invoice Number" -Name "txtInvoiceNumber" -Top 20 -AccessibleName "Invoice Number"
$customer = Add-Field -Caption "Customer Name" -Name "txtCustomerName" -Top 60 -AccessibleName "Customer Name"
$amount = Add-Field -Caption "Amount" -Name "txtAmount" -Top 100 -AccessibleName "Amount"
$notes = Add-Field -Caption "Notes" -Name "txtNotes" -Top 140 -AccessibleName "Notes"

$save = New-Object System.Windows.Forms.Button
$save.Name = "btnSave"
$save.Text = "Save"
$save.AccessibleName = "Save"
$save.Left = 150
$save.Top = 190
$save.Width = 100
[void]$form.Controls.Add($save)

$clear = New-Object System.Windows.Forms.Button
$clear.Name = "btnClear"
$clear.Text = "Clear"
$clear.AccessibleName = "Clear"
$clear.Left = 260
$clear.Top = 190
$clear.Width = 100
[void]$form.Controls.Add($clear)

$status = New-Object System.Windows.Forms.Label
$status.Name = "lblStatus"
$status.Text = "Status: ready"
$status.AccessibleName = "Status"
$status.Left = 20
$status.Top = 235
$status.Width = 470
[void]$form.Controls.Add($status)

$saved = New-Object System.Windows.Forms.ListBox
$saved.Name = "lstSaved"
$saved.AccessibleName = "Saved records"
$saved.Left = 20
$saved.Top = 260
$saved.Width = 470
$saved.Height = 140
[void]$form.Controls.Add($saved)

$clearFields = {
    $invoice.Clear()
    $customer.Clear()
    $amount.Clear()
    $notes.Clear()
    $invoice.Focus()
}

$save.Add_Click({
    if ([string]::IsNullOrWhiteSpace($invoice.Text)) {
        $status.Text = "Status: invoice number is required"
        return
    }
    $record = "{0} | {1} | {2}" -f $invoice.Text, $customer.Text, $amount.Text
    [void]$saved.Items.Add($record)
    $status.Text = "Status: saved " + (Get-Date -Format "HH:mm:ss")
    & $clearFields
})

$clear.Add_Click({
    $status.Text = "Status: cleared"
    & $clearFields
})

$invoice.Focus()
[void]$form.ShowDialog()
