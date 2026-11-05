module semi_interp_assemble_box_projector (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_dim,
    input  logic        in_p_stored_as_transpose,
    input  logic [1:0]  in_cdir,
    input  logic signed [31:0] in_index_x,
    input  logic signed [31:0] in_index_y,
    input  logic signed [31:0] in_index_z,
    input  logic signed [31:0] in_stride_x,
    input  logic signed [31:0] in_stride_y,
    input  logic signed [31:0] in_stride_z,
    input  logic signed [31:0] in_fine_imin_x,
    input  logic signed [31:0] in_fine_imin_y,
    input  logic signed [31:0] in_fine_imin_z,
    input  logic signed [31:0] in_fine_imax_x,
    input  logic signed [31:0] in_fine_imax_y,
    input  logic signed [31:0] in_fine_imax_z,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [31:0] out_send_imin_x,
    output logic signed [31:0] out_send_imin_y,
    output logic signed [31:0] out_send_imin_z,
    output logic signed [31:0] out_send_imax_x,
    output logic signed [31:0] out_send_imax_y,
    output logic signed [31:0] out_send_imax_z,
    output logic signed [31:0] out_recv_imin_x,
    output logic signed [31:0] out_recv_imin_y,
    output logic signed [31:0] out_recv_imin_z,
    output logic signed [31:0] out_recv_imax_x,
    output logic signed [31:0] out_recv_imax_y,
    output logic signed [31:0] out_recv_imax_z,
    output logic signed [31:0] out_sendr_imin_x,
    output logic signed [31:0] out_sendr_imin_y,
    output logic signed [31:0] out_sendr_imin_z,
    output logic signed [31:0] out_sendr_imax_x,
    output logic signed [31:0] out_sendr_imax_y,
    output logic signed [31:0] out_sendr_imax_z,
    output logic signed [31:0] out_recvr_imin_x,
    output logic signed [31:0] out_recvr_imin_y,
    output logic signed [31:0] out_recvr_imin_z,
    output logic signed [31:0] out_recvr_imax_x,
    output logic signed [31:0] out_recvr_imax_y,
    output logic signed [31:0] out_recvr_imax_z
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic signed [31:0] project_endpoint(
    input logic signed [33:0] lo, hi,
    input logic signed [31:0] index, stride,
    input logic upper
);
    logic signed [33:0] n, d, q, rem;
    begin
        if (lo > hi || stride == 0) begin
            project_endpoint = upper ? -32'sd1 : 32'sd0;
        end else begin
            d = {{2{stride[31]}}, stride};
            n = ((stride > 0) == upper ? hi : lo) - $signed({{2{index[31]}},index});
            q = n / d;
            rem = n % d;
            // RTL division truncates; lattice bounds require floor/ceil.
            if (rem != 0) begin
                if (upper && (n[33] != d[33])) q = q - 1;
                if (!upper && (n[33] == d[33])) q = q + 1;
            end
            project_endpoint = q[31:0];
        end
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    logic [1:0] dim_u;
    logic [1:0] cdir_u;
    logic       transpose_u;
    logic signed [33:0] grown_imin_x_34;
    logic signed [33:0] grown_imax_x_34;
    logic signed [33:0] grown_imin_y_34;
    logic signed [33:0] grown_imax_y_34;
    logic signed [33:0] grown_imin_z_34;
    logic signed [33:0] grown_imax_z_34;
    logic [1:0] ghost_lo_x;
    logic [1:0] ghost_hi_x;
    logic [1:0] ghost_lo_y;
    logic [1:0] ghost_hi_y;
    logic [1:0] ghost_lo_z;
    logic [1:0] ghost_hi_z;
    logic signed [31:0] proj_min_x;
    logic signed [31:0] proj_max_x;
    logic signed [31:0] proj_min_y;
    logic signed [31:0] proj_max_y;
    logic signed [31:0] proj_min_z;
    logic signed [31:0] proj_max_z;

    if (rst) begin
        busy <= 1'b0;

        out_send_imin_x  <= 32'sd0;
        out_send_imin_y  <= 32'sd0;
        out_send_imin_z  <= 32'sd0;
        out_send_imax_x  <= 32'sd0;
        out_send_imax_y  <= 32'sd0;
        out_send_imax_z  <= 32'sd0;
        out_recv_imin_x  <= 32'sd0;
        out_recv_imin_y  <= 32'sd0;
        out_recv_imin_z  <= 32'sd0;
        out_recv_imax_x  <= 32'sd0;
        out_recv_imax_y  <= 32'sd0;
        out_recv_imax_z  <= 32'sd0;
        out_sendr_imin_x <= 32'sd0;
        out_sendr_imin_y <= 32'sd0;
        out_sendr_imin_z <= 32'sd0;
        out_sendr_imax_x <= 32'sd0;
        out_sendr_imax_y <= 32'sd0;
        out_sendr_imax_z <= 32'sd0;
        out_recvr_imin_x <= 32'sd0;
        out_recvr_imin_y <= 32'sd0;
        out_recvr_imin_z <= 32'sd0;
        out_recvr_imax_x <= 32'sd0;
        out_recvr_imax_y <= 32'sd0;
        out_recvr_imax_z <= 32'sd0;
    end else if (input_accepted) begin
        dim_u       = in_dim;
        cdir_u      = in_cdir;
        transpose_u = in_p_stored_as_transpose;

        ghost_lo_x = (dim_u >= 2'd1) ? 2'd1 : 2'd0;
        ghost_hi_x = (dim_u >= 2'd1) ? 2'd1 : 2'd0;
        ghost_lo_y = (dim_u >= 2'd2) ? 2'd1 : 2'd0;
        ghost_hi_y = (dim_u >= 2'd2) ? 2'd1 : 2'd0;
        ghost_lo_z = (dim_u >= 2'd3) ? 2'd1 : 2'd0;
        ghost_hi_z = (dim_u >= 2'd3) ? 2'd1 : 2'd0;

        if (transpose_u) begin
            if ((cdir_u == 2'd0) && (dim_u >= 2'd1)) begin
                ghost_lo_x = 2'd2;
                ghost_hi_x = 2'd2;
            end else if ((cdir_u == 2'd1) && (dim_u >= 2'd2)) begin
                ghost_lo_y = 2'd2;
                ghost_hi_y = 2'd2;
            end else if ((cdir_u == 2'd2) && (dim_u >= 2'd3)) begin
                ghost_lo_z = 2'd2;
                ghost_hi_z = 2'd2;
            end
        end

        grown_imin_x_34 = $signed({{2{in_fine_imin_x[31]}}, in_fine_imin_x}) - $signed({32'd0, ghost_lo_x});
        grown_imax_x_34 = $signed({{2{in_fine_imax_x[31]}}, in_fine_imax_x}) + $signed({32'd0, ghost_hi_x});
        grown_imin_y_34 = $signed({{2{in_fine_imin_y[31]}}, in_fine_imin_y}) - $signed({32'd0, ghost_lo_y});
        grown_imax_y_34 = $signed({{2{in_fine_imax_y[31]}}, in_fine_imax_y}) + $signed({32'd0, ghost_hi_y});
        grown_imin_z_34 = $signed({{2{in_fine_imin_z[31]}}, in_fine_imin_z}) - $signed({32'd0, ghost_lo_z});
        grown_imax_z_34 = $signed({{2{in_fine_imax_z[31]}}, in_fine_imax_z}) + $signed({32'd0, ghost_hi_z});


        proj_min_x = project_endpoint(grown_imin_x_34, grown_imax_x_34, in_index_x, in_stride_x, 1'b0);
        proj_max_x = project_endpoint(grown_imin_x_34, grown_imax_x_34, in_index_x, in_stride_x, 1'b1);
        proj_min_y = project_endpoint(grown_imin_y_34, grown_imax_y_34, in_index_y, in_stride_y, 1'b0);
        proj_max_y = project_endpoint(grown_imin_y_34, grown_imax_y_34, in_index_y, in_stride_y, 1'b1);
        proj_min_z = project_endpoint(grown_imin_z_34, grown_imax_z_34, in_index_z, in_stride_z, 1'b0);
        proj_max_z = project_endpoint(grown_imin_z_34, grown_imax_z_34, in_index_z, in_stride_z, 1'b1);

        out_send_imin_x  <= proj_min_x;
        out_send_imin_y  <= proj_min_y;
        out_send_imin_z  <= proj_min_z;
        out_send_imax_x  <= proj_max_x;
        out_send_imax_y  <= proj_max_y;
        out_send_imax_z  <= proj_max_z;

        out_recv_imin_x  <= proj_min_x;
        out_recv_imin_y  <= proj_min_y;
        out_recv_imin_z  <= proj_min_z;
        out_recv_imax_x  <= proj_max_x;
        out_recv_imax_y  <= proj_max_y;
        out_recv_imax_z  <= proj_max_z;

        out_sendr_imin_x <= proj_min_x;
        out_sendr_imin_y <= proj_min_y;
        out_sendr_imin_z <= proj_min_z;
        out_sendr_imax_x <= proj_max_x;
        out_sendr_imax_y <= proj_max_y;
        out_sendr_imax_z <= proj_max_z;

        out_recvr_imin_x <= proj_min_x;
        out_recvr_imin_y <= proj_min_y;
        out_recvr_imin_z <= proj_min_z;
        out_recvr_imax_x <= proj_max_x;
        out_recvr_imax_y <= proj_max_y;
        out_recvr_imax_z <= proj_max_z;

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
