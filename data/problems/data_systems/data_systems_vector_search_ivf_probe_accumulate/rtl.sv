module data_systems_vector_search_ivf_probe_accumulate (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_key,
    input  logic         in_list_empty,
    input  logic [31:0]  in_scan_count,
    input  logic         in_last_probe,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_nlist_visited,
    output logic [31:0]  out_ndis
);

logic [31:0] nlist_visited_accum;
logic [31:0] ndis_accum;
logic        busy;

logic        contributes;
logic [31:0] visited_inc;
logic [31:0] ndis_inc;
logic [31:0] nlist_visited_next;
logic [31:0] ndis_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

// N:1 accumulator: accept inputs continuously unless holding a pending final output.
assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    contributes = 1'b0;
    visited_inc = 32'h00000000;
    ndis_inc = 32'h00000000;
    nlist_visited_next = nlist_visited_accum;
    ndis_next = ndis_accum;

    if ((in_key[31] == 1'b0) && (in_list_empty == 1'b0)) begin
        contributes = 1'b1;
    end

    if (contributes) begin
        visited_inc = 32'h00000001;
        ndis_inc = in_scan_count;
    end

    nlist_visited_next = nlist_visited_accum + visited_inc;
    ndis_next = ndis_accum + ndis_inc;
end

always_ff @(posedge clk) begin
    if (rst) begin
        nlist_visited_accum <= 32'h00000000;
        ndis_accum          <= 32'h00000000;
        busy                <= 1'b0;
        out_nlist_visited   <= 32'h00000000;
        out_ndis            <= 32'h00000000;
    end else begin
        if (input_accepted) begin
            if (in_last_probe) begin
                out_nlist_visited <= nlist_visited_next;
                out_ndis          <= ndis_next;
                nlist_visited_accum <= 32'h00000000;
                ndis_accum          <= 32'h00000000;
                busy                <= 1'b1;
            end else begin
                nlist_visited_accum <= nlist_visited_next;
                ndis_accum          <= ndis_next;
                busy                <= 1'b0;
            end
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
end

endmodule