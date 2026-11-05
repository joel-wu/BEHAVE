module ai_ml_arch_buffer_bank_bank_conflict_arbiter (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic in_single_ported,
    input  logic in_ex_read_valid,
    input  logic [7:0] in_ex_read_bank,
    input  logic in_dma_read_valid,
    input  logic [7:0] in_dma_read_bank,
    input  logic in_ex_write_valid,
    input  logic [7:0] in_ex_write_bank,
    input  logic in_dma_write_valid,
    input  logic [7:0] in_dma_write_bank,
    output logic out_valid,
    input  logic out_ready,
    output logic out_ex_read_grant,
    output logic out_dma_read_grant,
    output logic out_ex_write_grant,
    output logic out_dma_write_grant,
    output logic out_ex_read_blocked,
    output logic out_dma_read_blocked
);

    logic occupied;
    logic ex_write, dma_write, ex_read, dma_read;
    logic ex_blocked, dma_blocked;
    assign in_ready = !rst && (!occupied || out_ready);
    assign out_valid = occupied;

    always_comb begin
        ex_write = in_ex_write_valid;
        dma_write = in_dma_write_valid &&
            !(ex_write && in_ex_write_bank == in_dma_write_bank);
        ex_read = in_ex_read_valid;
        dma_read = in_dma_read_valid &&
            !(ex_read && in_ex_read_bank == in_dma_read_bank);
        ex_blocked = in_single_ported && ex_read &&
            ((ex_write && in_ex_write_bank == in_ex_read_bank) ||
             (dma_write && in_dma_write_bank == in_ex_read_bank));
        dma_blocked = in_single_ported && dma_read &&
            ((ex_write && in_ex_write_bank == in_dma_read_bank) ||
             (dma_write && in_dma_write_bank == in_dma_read_bank));
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            occupied <= 1'b0;
            out_ex_read_grant <= 1'b0;
            out_dma_read_grant <= 1'b0;
            out_ex_write_grant <= 1'b0;
            out_dma_write_grant <= 1'b0;
            out_ex_read_blocked <= 1'b0;
            out_dma_read_blocked <= 1'b0;
        end else if (in_valid && in_ready) begin
            occupied <= 1'b1;
            out_ex_read_grant <= ex_read && !ex_blocked;
            out_dma_read_grant <= dma_read && !dma_blocked;
            out_ex_write_grant <= ex_write;
            out_dma_write_grant <= dma_write;
            out_ex_read_blocked <= ex_blocked;
            out_dma_read_blocked <= dma_blocked;
        end else if (out_valid && out_ready) begin
            occupied <= 1'b0;
            out_ex_read_grant <= 1'b0;
            out_dma_read_grant <= 1'b0;
            out_ex_write_grant <= 1'b0;
            out_dma_write_grant <= 1'b0;
            out_ex_read_blocked <= 1'b0;
            out_dma_read_blocked <= 1'b0;
        end
    end
endmodule
